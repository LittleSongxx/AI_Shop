"""Run RGB-zh as supplied-evidence RAG transfer, never as a Search benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.rag.prompt_builder import RAG_REFUSAL_TEXT
from app.services.response_verifier import response_verifier
from evaluation.adapters.rag import RagGenerationError, _generate
from evaluation.core.io import (
    atomic_write_json,
    atomic_write_jsonl,
    canonical_json_bytes,
    load_json,
    load_jsonl,
    sha256_bytes,
    sha256_file,
)
from evaluation.public_transfer import (
    GOVERNANCE,
    MANIFEST_SCHEMA_VERSION,
    SCORER_VERSION,
    run_import,
)

RGB_REVISION = "65ec39e40e7dc9abb50e9bf1b4f32be3f6f16615"
RGB_INVENTORY_SHA256 = "2de09161c4b30c0c8437d4d4da41544b0f6770da14a8919487917374bd547199"
RGB_FILES = {
    "zh_refine.json": "7a5c7547dcfa1b83429b53a41644453508f820f502d3817312cbf8051afb4563",
    "zh_int.json": "e2ddb0461e2130f4b006996a574a89d5898989ff8b15f51d900c17162c7d1b17",
    "zh_fact.json": "171c4bc97860635092ce099f2da095c6ba8d07f0c6f7a19d0231373192f07da0",
}
MODES = ("refine", "refusal", "integration", "counterfactual")
AGENT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    AGENT_ROOT
    / "run/public-benchmarks/upstream/rgb-zh"
    / RGB_REVISION
)
DEFAULT_OUTPUT = AGENT_ROOT / "run/public-benchmarks/rgb-rag-v1"
_FORBIDDEN_KEYS = {"query", "answer", "snippet", "comment", "reason", "caseId"}


def _read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number} must be an object")
        rows.append(value)
    return rows


def _validate_inputs(root: Path) -> None:
    for name, expected in RGB_FILES.items():
        path = root / name
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"RGB input is missing or differs from pinned revision: {name}")


def _strings(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"RGB {field} must be a non-empty string list")
    return list(value)


def _answer_groups(value: Any) -> list[list[str]]:
    values = value if isinstance(value, list) else [value]
    groups: list[list[str]] = []
    for item in values:
        aliases = item if isinstance(item, list) else [item]
        if not aliases or any(not isinstance(alias, str) or not alias for alias in aliases):
            raise ValueError("RGB answer groups must contain non-empty strings")
        groups.append(list(aliases))
    if not groups:
        raise ValueError("RGB answer groups must not be empty")
    return groups


def _select_documents(
    row: Mapping[str, Any],
    *,
    mode: str,
    passage_count: int,
    noise_rate: float,
    seed: int,
) -> list[str]:
    """Mirror RGB's supplied-document construction with an isolated RNG."""

    if passage_count < 1 or not 0 <= noise_rate <= 1:
        raise ValueError("passage_count must be positive and noise_rate within [0,1]")
    rng = random.Random(seed)
    negative = _strings(row.get("negative"), field="negative")
    if mode == "integration":
        positive = row.get("positive")
        if not isinstance(positive, list) or any(not isinstance(group, list) for group in positive):
            raise ValueError("RGB integration positives must be grouped lists")
        groups = [_strings(group, field="positive group") for group in positive]
        for group in groups:
            rng.shuffle(group)
        documents = [group[0] for group in groups]
        target_positive = max(
            len(documents), passage_count - math.ceil(passage_count * noise_rate)
        )
        depth = 1
        while len(documents) < target_positive and any(
            len(group) > depth for group in groups
        ):
            for group in groups:
                if len(group) > depth and len(documents) < target_positive:
                    documents.append(group[depth])
            depth += 1
        documents.extend(negative[: max(0, passage_count - len(documents))])
        documents = documents[:passage_count]
    elif mode == "counterfactual":
        positive = _strings(row.get("positive"), field="positive")
        wrong = _strings(row.get("positive_wrong"), field="positive_wrong")
        if len(positive) != len(wrong):
            raise ValueError("RGB counterfactual positive pairs must align")
        negative_count = math.ceil(passage_count * noise_rate)
        wrong_count = max(0, passage_count - negative_count)
        selected = rng.sample(range(len(positive)), min(len(positive), wrong_count))
        documents = [wrong[index] for index in selected]
        documents.extend(negative[:negative_count])
    else:
        positive = _strings(row.get("positive"), field="positive")
        if mode == "refusal":
            documents = negative[:passage_count]
            rng.shuffle(documents)
            return documents
        negative_count = math.ceil(passage_count * noise_rate)
        positive_count = passage_count - negative_count
        if negative_count > len(negative):
            negative_count = len(negative)
            positive_count = passage_count - negative_count
        elif positive_count > len(positive):
            positive_count = len(positive)
            negative_count = passage_count - positive_count
        documents = positive[:positive_count] + negative[:negative_count]
    rng.shuffle(documents)
    return documents


def _case_key(mode: str, source_id: Any) -> str:
    digest = sha256_bytes(f"{mode}:{source_id}".encode("utf-8"))[:24]
    return f"rgb-zh:{mode}:{digest}"


def _retrieval(documents: Sequence[str], *, insufficient: bool) -> dict[str, Any]:
    items = []
    refs = []
    for citation, document in enumerate(documents, 1):
        document_key = sha256_bytes(document.encode("utf-8"))[:24]
        ref = {
            "id": f"rgb-doc:{document_key}",
            "source": "RGB_ZH_PUBLIC_TRANSFER",
            "heading": f"provided-document-{citation}",
        }
        items.append({"citation": citation, "text": document, "ref": ref})
        refs.append({"type": "public-transfer", "source": ref["source"], "id": ref["id"]})
    return {
        "evidenceState": "INSUFFICIENT" if insufficient else "SUPPORTED",
        "evidenceItems": items,
        "source_refs": refs,
        "trace": {"mode": "public_supplied_evidence"},
    }


def _compact(value: str) -> str:
    return "".join(value.casefold().split())


async def _case_rows(
    row: Mapping[str, Any],
    *,
    mode: str,
    passage_count: int,
    noise_rate: float,
    seed: int,
) -> list[dict[str, Any]]:
    documents = _select_documents(
        row,
        mode=mode,
        passage_count=passage_count,
        noise_rate=noise_rate,
        seed=seed,
    )
    retrieval = _retrieval(documents, insufficient=mode == "refusal")
    generated, generation = await _generate(str(row.get("query") or ""), retrieval)
    refs = list(retrieval["source_refs"])
    verified = response_verifier.verify(
        assistant=generated,
        biz_type="rag_public_transfer",
        tools_called=[],
        source_refs={"ragSources": refs, "businessSources": []},
        has_pending_action=False,
        policy_evidence_required=False,
        rag_citation_required=mode != "refusal",
        rag_evidence_state=retrieval["evidenceState"],
        rag_generation_verified=not bool(generation.get("repairRemaining")),
        rag_source_refs=refs,
    )
    terminal = verified.assistant
    key = _case_key(mode, row.get("id"))
    if mode == "refusal":
        answer_row = {
            "kind": "claim_or_span_case",
            "caseKey": key,
            "slice": "rgb-zh-refusal-exact",
            "task": "exact_or_alias",
            "prediction": terminal,
            "goldAnswers": [RAG_REFUSAL_TEXT],
        }
    else:
        answer_row = {
            "kind": "claim_or_span_case",
            "caseKey": key,
            "slice": f"rgb-zh-{mode}-answer",
            "task": "answer_groups",
            "prediction": terminal,
            "goldAnswerGroups": _answer_groups(row.get("answer")),
        }
    output = [
        answer_row,
        {
            "kind": "claim_or_span_case",
            "caseKey": f"{key}/verifier",
            "slice": f"rgb-zh-{mode}-verifier",
            "task": "binary_classification",
            "goldPositive": True,
            "predictedPositive": verified.passed,
        },
    ]
    if mode == "counterfactual":
        falsehood = str(row.get("fakeanswer") or "")
        if not falsehood:
            raise ValueError("RGB counterfactual fakeanswer must be non-empty")
        output.append(
            {
                "kind": "claim_or_span_case",
                "caseKey": f"{key}/falsehood-avoidance",
                "slice": "rgb-zh-counterfactual-falsehood-avoidance",
                "task": "binary_classification",
                "goldPositive": True,
                "predictedPositive": _compact(falsehood) not in _compact(terminal),
            }
        )
    return output


def _assert_safe_output(value: Any) -> None:
    if isinstance(value, Mapping):
        if _FORBIDDEN_KEYS.intersection(value):
            raise ValueError("RGB transfer output contains a forbidden field")
        for child in value.values():
            _assert_safe_output(child)
    elif isinstance(value, list):
        for child in value:
            _assert_safe_output(child)


def _fingerprint(config: Mapping[str, Any]) -> str:
    settings = get_settings()
    files = [
        Path(__file__),
        AGENT_ROOT / "evaluation/adapters/rag.py",
        AGENT_ROOT / "app/rag/prompt_builder.py",
        AGENT_ROOT / "app/services/response_verifier.py",
    ]
    return sha256_bytes(
        canonical_json_bytes(
            {
                "adapter": "aishop-rgb-rag-transfer/v1",
                "config": dict(config),
                "llmModel": settings.llm_model,
                "llmEndpointSha256": sha256_bytes(settings.llm_base_url.encode("utf-8")),
                "code": {path.name: sha256_file(path) for path in files},
            }
        )
    )


def _write_manifest(
    path: Path,
    *,
    normalized_path: Path,
    rows: Sequence[dict[str, Any]],
    config: Mapping[str, Any],
    fingerprint: str,
) -> None:
    manifest = {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "datasetId": "rgb-zh-aishop-supplied-evidence-rag",
        "officialUrl": "https://github.com/chen700564/RGB",
        "license": "CC-BY-NC-SA-4.0_NONCOMMERCIAL_EXPLORATION_ONLY",
        "upstreamRevisionOrCommit": RGB_REVISION,
        "perFileInventoryOrCanonicalInventorySha256": RGB_INVENTORY_SHA256,
        "selectionPolicy": (
            "Pinned RGB Chinese refined/integration/counterfactual rows; deterministic supplied-"
            "document construction; dataset-oracle evidence state for refusal; measures AI-Shop "
            "grounding/generation/repair/response-verifier only, never Search. Official RGB "
            "ChatGPT rejection/error judge is shadow-only and NOT_RUN here; deterministic answer "
            "coverage, exact refusal, verifier agreement, and falsehood avoidance are reported. "
            "public-transfer, post-hoc, exploratory, non-release-gate, noncommercial. "
            f"config={json.dumps(dict(config), sort_keys=True, separators=(',', ':'))}"
        ),
        "scorerVersion": SCORER_VERSION,
        "modelAndPromptFingerprintOrNOT_APPLICABLE": fingerprint,
        "caseCountAndEligibleDenominators": {
            "caseCount": len({(row["kind"], row["caseKey"]) for row in rows}),
            "rankingCaseEligible": 0,
            "gradedRankingCaseEligible": 0,
            "binaryRankingCaseEligible": 0,
            "claimOrSpanCaseEligible": len(rows),
            "agentTrialEligible": 0,
            "agentCaseEligible": 0,
        },
        "normalizedInputSha256": sha256_file(normalized_path),
        "exhaustiveClaimGold": False,
        "exhaustiveCitationGold": False,
        "officialAgentExecution": False,
        **GOVERNANCE,
    }
    _assert_safe_output(manifest)
    atomic_write_json(path, manifest)


async def run(
    *,
    input_root: Path,
    output: Path,
    modes: Sequence[str],
    limit_per_slice: int,
    passage_count: int,
    refine_noise_rate: float,
    counterfactual_noise_rate: float,
    seed: int,
    resume: bool,
) -> Path:
    _validate_inputs(input_root)
    if not get_settings().llm_api_key.strip():
        raise RuntimeError("real main LLM Provider is not configured")
    if limit_per_slice < 0:
        raise ValueError("limit_per_slice must be non-negative")
    unknown = set(modes) - set(MODES)
    if unknown:
        raise ValueError(f"unsupported RGB modes: {sorted(unknown)}")
    normalized_path = output / "normalized.jsonl"
    manifest_path = output / "source-manifest.json"
    config = {
        "modes": list(modes),
        "limitPerSlice": limit_per_slice,
        "passageCount": passage_count,
        "refineNoiseRate": refine_noise_rate,
        "counterfactualNoiseRate": counterfactual_noise_rate,
        "seed": seed,
    }
    fingerprint = _fingerprint(config)
    if (normalized_path.exists() or manifest_path.exists()) and not resume:
        raise FileExistsError("normalized output exists; pass --resume or choose a new output")
    if resume and normalized_path.exists() != manifest_path.exists():
        raise ValueError("resume requires both normalized rows and their source manifest")
    if resume and manifest_path.exists():
        prior = load_json(manifest_path)
        if prior.get("modelAndPromptFingerprintOrNOT_APPLICABLE") != fingerprint:
            raise ValueError("resume model, prompt, code, or run configuration changed")
    rows = load_jsonl(normalized_path) if normalized_path.exists() else []
    _assert_safe_output(rows)
    completed = {(row["caseKey"], row.get("task")) for row in rows}
    file_for_mode = {
        "refine": "zh_refine.json",
        "refusal": "zh_refine.json",
        "integration": "zh_int.json",
        "counterfactual": "zh_fact.json",
    }
    for mode in modes:
        source_rows = _read_rows(input_root / file_for_mode[mode])
        if limit_per_slice:
            source_rows = source_rows[:limit_per_slice]
        for index, source in enumerate(source_rows, 1):
            key = _case_key(mode, source.get("id"))
            expected = {(key, "exact_or_alias" if mode == "refusal" else "answer_groups")}
            expected.add((f"{key}/verifier", "binary_classification"))
            if mode == "counterfactual":
                expected.add((f"{key}/falsehood-avoidance", "binary_classification"))
            if expected.issubset(completed):
                continue
            if completed.intersection(expected):
                raise ValueError("resume input contains a partial RGB case")
            case_rows = await _case_rows(
                source,
                mode=mode,
                passage_count=passage_count,
                noise_rate=(
                    1.0
                    if mode == "refusal"
                    else counterfactual_noise_rate
                    if mode == "counterfactual"
                    else refine_noise_rate
                ),
                seed=seed,
            )
            _assert_safe_output(case_rows)
            rows.extend(case_rows)
            completed.update((row["caseKey"], row.get("task")) for row in case_rows)
            atomic_write_jsonl(normalized_path, rows)
            _write_manifest(
                manifest_path,
                normalized_path=normalized_path,
                rows=rows,
                config=config,
                fingerprint=fingerprint,
            )
            print(f"{mode}: completed {index}/{len(source_rows)}")
    _write_manifest(
        manifest_path,
        normalized_path=normalized_path,
        rows=rows,
        config=config,
        fingerprint=fingerprint,
    )
    return run_import(
        manifest_path=manifest_path,
        input_path=normalized_path,
        output=output / "report",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--mode", action="append", choices=MODES, dest="modes")
    parser.add_argument(
        "--limit-per-slice",
        type=int,
        default=5,
        help="safe pilot default; use 0 only for the full 300/300/100/100 run",
    )
    parser.add_argument("--passage-count", type=int, default=5)
    parser.add_argument("--refine-noise-rate", type=float, default=0.6)
    parser.add_argument("--counterfactual-noise-rate", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=2333)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(
            run(
                input_root=args.input_root,
                output=args.output,
                modes=args.modes or MODES,
                limit_per_slice=args.limit_per_slice,
                passage_count=args.passage_count,
                refine_noise_rate=args.refine_noise_rate,
                counterfactual_noise_rate=args.counterfactual_noise_rate,
                seed=args.seed,
                resume=args.resume,
            )
        )
    except RagGenerationError as exc:
        raise SystemExit(
            f"provider generation failed ({type(exc.cause).__name__}); rerun with --resume"
        ) from None
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
