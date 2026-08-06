from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DATASET = Path(__file__).with_name("rag_golden.jsonl")
DEFAULT_LOCK = Path(__file__).with_name("rag_golden.lock.json")


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            cases.append(json.loads(line))
    return cases


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_knowledge_text(value: str) -> str:
    lines = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result: list[str] = []
    previous_blank = False
    for line in lines:
        normalized = re.sub(r"[\t ]+", " ", line.replace("\u00a0", " ")).strip()
        if not normalized:
            if result and not previous_blank:
                result.append("")
            previous_blank = True
            continue
        result.append(normalized)
        previous_blank = False
    return "\n".join(result).strip()


def normalized_knowledge_sha256(path: Path) -> str:
    normalized = normalized_knowledge_text(path.read_text(encoding="utf-8"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def validate_local_contract(dataset: Path, lock_path: Path) -> dict[str, Any]:
    from app.rag.evaluation import placeholder_references

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if lock.get("schemaVersion") != 1:
        errors.append("unsupported RAG lock schema")
    actual_dataset_sha = sha256_file(dataset)
    if lock.get("datasetSha256") != actual_dataset_sha:
        errors.append(
            f"dataset SHA mismatch: expected {lock.get('datasetSha256')}, got {actual_dataset_sha}"
        )
    cases = load_cases(dataset)
    placeholders = placeholder_references(cases)
    if placeholders:
        errors.append(f"dataset contains placeholder references: {placeholders[:3]}")
    ids = [str(case.get("id") or "") for case in cases]
    if not ids or any(not case_id for case_id in ids) or len(ids) != len(set(ids)):
        errors.append("dataset case ids must be non-empty and unique")
    selected_threshold = lock.get("selectedThreshold")
    if selected_threshold is not None and not isinstance(selected_threshold, (int, float)):
        errors.append("selectedThreshold must be numeric")
    baseline = lock.get("qualityBaseline") or {}
    if baseline and (
        not isinstance(baseline, dict)
        or any(
            not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0
            for value in baseline.values()
        )
    ):
        errors.append("qualityBaseline values must be non-negative numbers")

    knowledge_files: list[dict[str, Any]] = []
    for expected in lock.get("knowledgeFiles") or []:
        path = (PROJECT_ROOT / str(expected.get("path") or "")).resolve()
        if not path.is_file():
            errors.append(f"knowledge file missing: {path}")
            continue
        raw_sha = sha256_file(path)
        normalized_sha = normalized_knowledge_sha256(path)
        if raw_sha != expected.get("rawSha256"):
            errors.append(f"knowledge raw SHA mismatch: {path.name}")
        if normalized_sha != expected.get("normalizedSha256"):
            errors.append(f"knowledge normalized SHA mismatch: {path.name}")
        knowledge_files.append(
            {
                "source": path.name,
                "rawSha256": raw_sha,
                "normalizedSha256": normalized_sha,
            }
        )
    if errors:
        raise ValueError("RAG evaluation contract invalid:\n- " + "\n- ".join(errors))
    return {
        "lock": lock,
        "datasetSha256": actual_dataset_sha,
        "knowledgeFiles": knowledge_files,
        "cases": len(cases),
    }


def select_threshold_result(
    scans: list[dict[str, Any]], lock: dict[str, Any]
) -> dict[str, Any]:
    frozen = lock.get("selectedThreshold")
    if frozen is not None:
        matches = [row for row in scans if abs(float(row["threshold"]) - float(frozen)) < 1e-9]
        if not matches:
            raise ValueError(f"threshold scan does not include frozen threshold {frozen}")
        return matches[0]

    qualified = [
        row for row in scans if row["recallAtK"] >= 0.80 and row["mrr"] >= 0.65
    ]
    return max(
        qualified,
        key=lambda row: (
            row["noAnswerF1"],
            row["answerCitationRate"],
            row["recallAtK"],
            row["threshold"],
        ),
        default=max(scans, key=lambda row: (row["recallAtK"], row["mrr"])),
    )


async def validate_live_contract(lock: dict[str, Any]) -> dict[str, Any]:
    from app.services.java_internal_client import java_internal_client

    catalog = await java_internal_client.knowledge_catalog()
    faq_rows = await java_internal_client.top_faq(100)
    errors: list[str] = []
    version = int(catalog.get("version") or 0)
    minimum_release = int(lock.get("minimumKnowledgeRelease") or 1)
    if version < minimum_release:
        errors.append(f"knowledge release {version} is below required {minimum_release}")

    actual_faq_ids = {
        str(row.get("question_id") or row.get("questionId") or "") for row in faq_rows
    }
    required_faq_ids = {str(value) for value in lock.get("requiredFaqQuestionIds") or []}
    missing_faq = sorted(required_faq_ids - actual_faq_ids)
    if missing_faq:
        errors.append(f"required FAQ ids are missing: {missing_faq}")

    documents = catalog.get("documents") or []
    by_source = {str(row.get("source_name") or ""): row for row in documents}
    for expected in lock.get("knowledgeFiles") or []:
        source = Path(str(expected.get("path") or "")).name
        actual = by_source.get(source)
        if not actual:
            errors.append(f"published knowledge source is missing: {source}")
            continue
        if actual.get("content_hash") != expected.get("normalizedSha256"):
            errors.append(f"published knowledge content hash mismatch: {source}")
    if errors:
        raise ValueError("live RAG release does not match the locked dataset:\n- " + "\n- ".join(errors))
    return {
        "knowledgeRelease": version,
        "activeDocumentIds": catalog.get("active_document_ids") or [],
        "publishedSources": sorted(by_source),
        "faqQuestionIds": sorted(required_faq_ids),
    }


def _candidate_results(
    raw_results: list[dict[str, Any]], threshold: float
) -> list[dict[str, Any]]:
    from app.config.settings import get_settings
    from app.rag.retriever import rrf_score_at_rank

    rrf_floor = rrf_score_at_rank(get_settings().rag_evidence_min_rrf_rank)
    results: list[dict[str, Any]] = []
    for raw in raw_results:
        candidates = raw.get("_evaluationCandidateRefs") or raw.get("source_refs") or []
        accepted = []
        for ref in candidates:
            score = float(ref.get("score") or 0)
            floor = rrf_floor if ref.get("retrieval") == "rrf" else threshold
            if score >= floor:
                accepted.append(ref)
        results.append(
            {
                "source_refs": accepted,
                "trace": {
                    **(raw.get("trace") or {}),
                    "hit": bool(accepted),
                    "sourceCount": len(accepted),
                },
            }
        )
    return results


async def run(dataset: Path, lock_path: Path, top_k: int, thresholds: list[float]) -> dict:
    from app.rag.evaluation import evaluate_results
    from app.rag.retriever import rag_retriever
    from app.services.redis_service import redis_service

    await redis_service.ensure_connected()
    try:
        local_contract = validate_local_contract(dataset, lock_path)
        live_contract = await validate_live_contract(local_contract["lock"])
        cases = load_cases(dataset)
        raw_results = [
            await rag_retriever.search_faq_with_trace(
                case.get("query") or "",
                top_k=top_k,
                include_evaluation_candidates=True,
            )
            for case in cases
        ]
        scans = []
        for threshold in sorted(set(round(value, 4) for value in thresholds)):
            metrics = evaluate_results(
                cases, _candidate_results(raw_results, threshold), top_k=top_k
            )
            scans.append({"threshold": threshold, **metrics})
        selected = select_threshold_result(scans, local_contract["lock"])
        baseline = local_contract["lock"].get("qualityBaseline") or {}
        regressions = {
            key: {"expected": float(value), "actual": float(selected.get(key) or 0)}
            for key, value in baseline.items()
            if key in selected and float(selected.get(key) or 0) < float(value)
        }
        return {
            "contract": {
                "datasetSha256": local_contract["datasetSha256"],
                "cases": local_contract["cases"],
                **live_contract,
            },
            "selectedThreshold": selected["threshold"],
            "metrics": selected,
            "thresholdScan": [
                {
                    "threshold": row["threshold"],
                    "recallAtK": row["recallAtK"],
                    "mrr": row["mrr"],
                    "noAnswerF1": row["noAnswerF1"],
                    "answerCitationRate": row["answerCitationRate"],
                }
                for row in scans
            ],
            "baselineRegressions": regressions,
        }
    finally:
        await redis_service.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the locked live FAQ/knowledge release.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--min-recall", type=float, default=0.80)
    parser.add_argument("--min-mrr", type=float, default=0.65)
    parser.add_argument("--min-no-answer-f1", type=float, default=0.80)
    parser.add_argument(
        "--thresholds",
        default="0.30,0.35,0.40,0.45,0.50,0.55,0.60,0.65,0.70,0.75,0.80,0.85",
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument("--no-fail", action="store_true")
    args = parser.parse_args()
    thresholds = [float(value) for value in args.thresholds.split(",") if value.strip()]
    report = asyncio.run(run(args.dataset, args.lock, args.top_k, thresholds))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.out:
        args.out.write_text(rendered + "\n", encoding="utf-8")
    metrics = report["metrics"]
    passed = (
        not metrics["placeholderRefs"]
        and not report["baselineRegressions"]
        and metrics["recallAtK"] >= args.min_recall
        and metrics["mrr"] >= args.min_mrr
        and metrics["noAnswerF1"] >= args.min_no_answer_f1
    )
    if not args.no_fail and not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
