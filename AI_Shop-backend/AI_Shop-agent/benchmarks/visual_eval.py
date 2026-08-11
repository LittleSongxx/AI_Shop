from __future__ import annotations

import hashlib
import io
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = Path(__file__).with_name("visual_relevance_v1.jsonl")
LOCK_PATH = Path(__file__).with_name("visual_relevance_v1.lock.json")
CATALOG_PATH = PROJECT_ROOT.parent / "data" / "simlect_catalog" / "catalog.json"
ASSET_ROOT = PROJECT_ROOT.parent / "data" / "simlect-assets" / "file"

REQUIRED_SUBSETS = frozenset(
    {
        "exact_image",
        "compressed_or_cropped",
        "alternate_view",
        "category_similarity",
        "no_match",
    }
)
_TRANSFORMS = frozenset({"identity", "jpeg", "crop"})
_SYNTHETIC_TYPES = frozenset({"solid", "checkerboard", "noise", "stripes"})


@dataclass(frozen=True)
class VisualEvalCase:
    case_id: str
    subset: str
    raw: dict[str, Any]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_cases(path: Path = DATASET_PATH) -> list[VisualEvalCase]:
    cases: list[VisualEvalCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_number} is not valid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}:{line_number} must contain an object")
        cases.append(
            VisualEvalCase(
                case_id=str(row.get("id") or ""),
                subset=str(row.get("subset") or ""),
                raw=row,
            )
        )
    return cases


def load_catalog(path: Path = CATALOG_PATH) -> tuple[dict[str, dict], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    products: dict[str, dict] = {}
    for detail in payload.get("products") or []:
        info = detail.get("productInfo") or {}
        product_id = str(info.get("productId") or "")
        if product_id:
            products[product_id] = info
    return products, payload


def validate_contract(
    cases: list[VisualEvalCase],
    *,
    dataset_path: Path = DATASET_PATH,
    lock_path: Path = LOCK_PATH,
    catalog_path: Path = CATALOG_PATH,
    asset_root: Path = ASSET_ROOT,
) -> dict[str, Any]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    products, catalog = load_catalog(catalog_path)
    errors: list[str] = []
    ids = [case.case_id for case in cases]
    if not cases or "" in ids or len(ids) != len(set(ids)):
        errors.append("cases must have unique non-empty IDs")
    if lock.get("schemaVersion") != 1:
        errors.append("unsupported visual relevance lock schema")
    if sha256_file(dataset_path) != lock.get("datasetSha256"):
        errors.append("visual dataset SHA does not match lock")
    if sha256_file(catalog_path) != lock.get("catalogSha256"):
        errors.append("catalog SHA does not match lock")
    if catalog.get("catalogVersion") != lock.get("catalogVersion"):
        errors.append("catalog version does not match lock")
    if len(cases) != int(lock.get("caseCount") or 0):
        errors.append("case count does not match lock")

    counts = {subset: 0 for subset in REQUIRED_SUBSETS}
    for case in cases:
        row = case.raw
        if case.subset not in REQUIRED_SUBSETS:
            errors.append(f"{case.case_id}: unknown subset {case.subset}")
            continue
        counts[case.subset] += 1
        if not str(row.get("note") or "").strip():
            errors.append(f"{case.case_id}: note is required")
        transform = row.get("transform") or {}
        if transform.get("type") not in _TRANSFORMS:
            errors.append(f"{case.case_id}: unsupported transform")
        expect_reject = row.get("expectReject")
        if not isinstance(expect_reject, bool):
            errors.append(f"{case.case_id}: expectReject must be boolean")
        grades = row.get("relevanceGrades")
        if not isinstance(grades, dict):
            errors.append(f"{case.case_id}: relevanceGrades must be an object")
            grades = {}
        target = row.get("targetProductId")
        if expect_reject:
            if target is not None or grades:
                errors.append(f"{case.case_id}: rejection case cannot label products")
            synthetic = row.get("synthetic") or {}
            if synthetic.get("type") not in _SYNTHETIC_TYPES:
                errors.append(f"{case.case_id}: rejection case needs valid synthetic input")
            continue
        target = str(target or "")
        if target not in products or target not in grades:
            errors.append(f"{case.case_id}: target product is absent from labels/catalog")
            continue
        if any(str(product_id) not in products for product_id in grades):
            errors.append(f"{case.case_id}: relevance label references unknown product")
        if any(
            not isinstance(grade, (int, float))
            or isinstance(grade, bool)
            or not 0 < float(grade) <= 3
            for grade in grades.values()
        ):
            errors.append(f"{case.case_id}: grades must be numbers in (0, 3]")
        asset = str(row.get("asset") or "")
        path = (asset_root / asset).resolve()
        if not asset or asset_root.resolve() not in path.parents or not path.is_file():
            errors.append(f"{case.case_id}: query asset is missing or escapes asset root")
            continue
        covers = [part.strip() for part in str(products[target].get("cover") or "").split(",")]
        if asset not in covers:
            errors.append(f"{case.case_id}: asset is not a cover of target product")

    if counts != lock.get("subsetCounts"):
        errors.append("subset counts do not match lock")
    if any(count < 5 for count in counts.values()):
        errors.append("every visual subset must contain at least five cases")
    if errors:
        raise ValueError("visual relevance contract invalid:\n- " + "\n- ".join(errors))
    return {
        "cases": len(cases),
        "subsetCounts": counts,
        "datasetSha256": sha256_file(dataset_path),
        "catalogSha256": sha256_file(catalog_path),
        "catalogVersion": catalog.get("catalogVersion"),
        "thresholds": lock.get("thresholds") or {},
    }


def build_query_image(case: VisualEvalCase, asset_root: Path = ASSET_ROOT) -> bytes:
    row = case.raw
    if row.get("synthetic"):
        image = _synthetic_image(row["synthetic"])
        original_format = "PNG"
    else:
        source = (asset_root / str(row["asset"])).resolve()
        raw = source.read_bytes()
        if (row.get("transform") or {}).get("type") == "identity":
            return raw
        image = Image.open(io.BytesIO(raw))
        image.load()
        original_format = image.format or "PNG"
    transform = row.get("transform") or {"type": "identity"}
    kind = transform.get("type")
    if kind == "crop":
        margin = float(transform.get("margin") or 0.08)
        if not 0 < margin < 0.25:
            raise ValueError(f"{case.case_id}: crop margin must be in (0, 0.25)")
        width, height = image.size
        cropped = image.crop(
            (
                round(width * margin),
                round(height * margin),
                round(width * (1 - margin)),
                round(height * (1 - margin)),
            )
        )
        image = cropped.resize((width, height), Image.Resampling.LANCZOS)
        original_format = "JPEG"
    if kind == "jpeg":
        original_format = "JPEG"
    output = io.BytesIO()
    if original_format.upper() == "JPEG":
        image.convert("RGB").save(
            output,
            format="JPEG",
            quality=int(transform.get("quality") or 88),
            optimize=True,
        )
    else:
        image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def evaluate_predictions(
    cases: list[VisualEvalCase],
    predictions: dict[str, list[str] | dict[str, Any]],
    *,
    fallback_outcomes: dict[str, bool] | None = None,
) -> dict[str, Any]:
    products, _catalog = load_catalog()
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    exact: list[bool] = []
    robustness: list[bool] = []
    alternate: list[bool] = []
    rejection: list[bool] = []
    cross_category = total_positive_results = unavailable_results = 0

    for case in cases:
        prediction = predictions.get(case.case_id, [])
        if isinstance(prediction, dict):
            ranked = [str(value) for value in prediction.get("rankedProductIds") or []]
            unavailable_results += len(prediction.get("unavailableProductIds") or [])
        else:
            ranked = [str(value) for value in prediction]
        ranked = list(dict.fromkeys(ranked))[:5]
        if case.raw.get("expectReject"):
            rejection.append(not ranked)
            continue
        grades = {
            str(product_id): float(grade)
            for product_id, grade in case.raw["relevanceGrades"].items()
        }
        first_rank = next(
            (rank for rank, product_id in enumerate(ranked, 1) if product_id in grades),
            None,
        )
        reciprocal_ranks.append(1 / first_rank if first_rank else 0.0)
        ndcgs.append(_ndcg(ranked, grades, 5))
        target = str(case.raw["targetProductId"])
        if case.subset == "exact_image":
            exact.append(bool(ranked) and ranked[0] == target)
        elif case.subset == "compressed_or_cropped":
            robustness.append(target in ranked)
        elif case.subset == "alternate_view":
            alternate.append(target in ranked)
        query_category = str(products[target].get("categoryId") or "")
        for product_id in ranked:
            total_positive_results += 1
            candidate = products.get(product_id)
            if candidate is None or str(candidate.get("categoryId") or "") != query_category:
                cross_category += 1

    fallback_values = list((fallback_outcomes or {}).values())
    return {
        "caseCount": len(cases),
        "exactTop1": _mean(exact),
        "robustnessRecallAt5": _mean(robustness),
        "alternateViewRecallAt5": _mean(alternate),
        "mrr": _mean(reciprocal_ranks),
        "ndcgAt5": _mean(ndcgs),
        "rejectionAccuracy": _mean(rejection),
        "crossCategoryFalsePositiveRate": (
            cross_category / total_positive_results if total_positive_results else 0.0
        ),
        "unavailableProductRate": (
            unavailable_results / (total_positive_results + unavailable_results)
            if total_positive_results + unavailable_results
            else 0.0
        ),
        "fallbackSuccessRate": _mean(fallback_values) if fallback_values else None,
    }


def gate_failures(report: dict[str, Any], thresholds: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    minimum_metrics = (
        "exactTop1",
        "robustnessRecallAt5",
        "alternateViewRecallAt5",
        "mrr",
        "ndcgAt5",
        "rejectionAccuracy",
        "fallbackSuccessRate",
    )
    maximum_metrics = ("crossCategoryFalsePositiveRate", "unavailableProductRate")
    for metric in minimum_metrics:
        threshold = thresholds.get(metric)
        if threshold is None:
            continue
        actual = report.get(metric)
        if actual is None or float(actual) < float(threshold):
            failures.append(f"{metric}={actual} is below {threshold}")
    for metric in maximum_metrics:
        threshold = thresholds.get(metric)
        if threshold is None:
            continue
        actual = report.get(metric)
        if actual is None or float(actual) > float(threshold):
            failures.append(f"{metric}={actual} exceeds {threshold}")
    return failures


def _synthetic_image(spec: dict[str, Any]) -> Image.Image:
    kind = spec.get("type")
    size = (640, 480)
    if kind == "solid":
        return Image.new("RGB", size, color=tuple(spec.get("color") or [128, 128, 128]))
    if kind == "noise":
        rng = random.Random(int(spec.get("seed") or 0))
        return Image.frombytes("RGB", size, rng.randbytes(size[0] * size[1] * 3))
    color_a = tuple(spec.get("colorA") or [0, 0, 0])
    color_b = tuple(spec.get("colorB") or [255, 255, 255])
    image = Image.new("RGB", size, color=color_a)
    draw = ImageDraw.Draw(image)
    if kind == "checkerboard":
        block = 40
        for y in range(0, size[1], block):
            for x in range(0, size[0], block):
                if (x // block + y // block) % 2:
                    draw.rectangle((x, y, x + block - 1, y + block - 1), fill=color_b)
        return image
    if kind == "stripes":
        for x in range(0, size[0], 32):
            if (x // 32) % 2:
                draw.rectangle((x, 0, x + 31, size[1]), fill=color_b)
        return image
    raise ValueError(f"unsupported synthetic type: {kind}")


def _dcg(gains: list[float]) -> float:
    return sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, 1))


def _ndcg(ranked: list[str], grades: dict[str, float], k: int) -> float:
    actual = [grades.get(product_id, 0.0) for product_id in ranked[:k]]
    ideal = sorted(grades.values(), reverse=True)[:k]
    ideal_dcg = _dcg(ideal)
    return _dcg(actual) / ideal_dcg if ideal_dcg else 0.0


def _mean(values: list[bool] | list[float]) -> float:
    return sum(float(value) for value in values) / len(values) if values else 0.0
