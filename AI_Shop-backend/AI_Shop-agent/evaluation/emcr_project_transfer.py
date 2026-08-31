"""Run eMCR text-only through the production AI-Shop Search design."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.rag.retriever import rag_retriever
from app.services.product_search_pipeline import (
    ProductRuntimeConstraints,
    build_product_query_plan,
    product_search_pipeline,
)
from evaluation.core.io import (
    AGENT_ROOT,
    atomic_write_json,
    canonical_json_bytes,
    load_json,
    load_jsonl,
    sha256_bytes,
    sha256_file,
)
from evaluation.public_transfer import GOVERNANCE, MANIFEST_SCHEMA_VERSION, SCORER_VERSION

ARMS = ("full", "no_fusion", "no_constraint_guard")
UPSTREAM_REVISION = "4db72e546befae3e076ac589e458a38cfd350bc3"
OFFICIAL_URL = "https://huggingface.co/datasets/alibabagroup/eMCR"
RUNNER_VERSION = "aishop-emcr-project-search-adapted-transfer/v1"


class EmcrProjectTransferError(ValueError):
    """Raised when inputs cannot support a paired project-design comparison."""


def _load_rankings(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in load_jsonl(path):
        if row.get("kind") != "ranking_case":
            raise EmcrProjectTransferError(f"{path.name}: expected ranking_case rows")
        key = str(row.get("caseKey") or "")
        ranking = row.get("ranking")
        qrels = row.get("qrels")
        if (
            len(key) != 64
            or any(character not in "0123456789abcdef" for character in key)
            or key in rows
            or not isinstance(ranking, list)
            or len(ranking) != len(set(ranking))
            or any(not isinstance(value, str) or not value for value in ranking)
            or not isinstance(qrels, dict)
            or not qrels
            or not isinstance(row.get("slice"), str)
        ):
            raise EmcrProjectTransferError(f"{path.name}: invalid normalized ranking row")
        rows[key] = row
    if not rows:
        raise EmcrProjectTransferError(f"{path.name}: no ranking rows")
    return rows


def _property_values(value: Any) -> list[dict[str, str]]:
    parsed: Any = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = value
    if isinstance(parsed, dict):
        return [
            {"property_name": str(name), "property_value": str(raw)} for name, raw in parsed.items()
        ]
    text = str(parsed or "").strip()
    return [{"property_name": "", "property_value": text}] if text else []


def _product(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": str(row["content_id"]),
        "product_name": str(row.get("title") or row.get("commodity_name") or ""),
        "product_desc": str(row.get("detail_ocr") or ""),
        "brand": str(row.get("std_brand_name") or ""),
        "category": str(row.get("cate_full_name") or ""),
        "price": row.get("reserve_price"),
        "property_values": _property_values(row.get("property_kvs")),
    }


def _load_projection(
    raw_path: Path,
    *,
    selected_keys: set[str],
    needed_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    cases: dict[str, dict[str, Any]] = {}
    products: dict[str, dict[str, Any]] = {}
    with raw_path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise EmcrProjectTransferError(
                    f"{raw_path.name}:{line_number}: invalid JSON"
                ) from exc
            if not isinstance(row, dict) or "image_clue" in str(row.get("task_type") or ""):
                continue
            document_id = str(row.get("content_id") or "")
            if document_id in needed_ids and document_id not in products:
                products[document_id] = _product(row)
            text = str(row.get("query") or "")
            key = hashlib.sha256(text.encode()).hexdigest()
            if key not in selected_keys:
                continue
            case = cases.setdefault(
                key,
                {
                    "text": text,
                    "slice": str(row.get("task_type") or ""),
                    "qrels": {},
                },
            )
            if case["text"] != text or case["slice"] != str(row.get("task_type") or ""):
                raise EmcrProjectTransferError("raw rows disagree within one public case")
            grade = int(row.get("score") or 0)
            previous = case["qrels"].setdefault(document_id, grade)
            if previous != grade:
                raise EmcrProjectTransferError("raw rows contain conflicting relevance grades")
    missing_cases = selected_keys - set(cases)
    missing_products = needed_ids - set(products)
    if missing_cases or missing_products:
        raise EmcrProjectTransferError(
            "raw projection does not cover all selected cases and candidates"
        )
    return cases, products


def _denominators(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "caseCount": len(rows),
        "rankingCaseEligible": len(rows),
        "gradedRankingCaseEligible": sum(
            any(int(grade) > 0 for grade in row["qrels"].values()) for row in rows
        ),
        "binaryRankingCaseEligible": sum(
            any(
                int(grade) >= int(row.get("relevanceThreshold", 1))
                for grade in row["qrels"].values()
            )
            for row in rows
        ),
        "claimOrSpanCaseEligible": 0,
        "agentTrialEligible": 0,
        "agentCaseEligible": 0,
    }


def _selection_policy(arm: str, *, case_limit: int, candidate_size: int, result_size: int) -> str:
    designs = {
        "full": "BM25 plus frozen BGE-M3 dense RRF, full query plan and constraint guard, fixed reranker",
        "no_fusion": "frozen BGE-M3 dense only, full query plan and constraint guard, fixed reranker",
        "no_constraint_guard": "BM25 plus frozen BGE-M3 dense RRF, empty constraint surface, fixed reranker",
    }
    return (
        "PROJECT_SEARCH_ADAPTED_TRANSFER; public-transfer; post-hoc; exploratory; "
        "non-release-gate; not full-stack and not an official leaderboard result. "
        f"Deterministic SHA-256 key prefix, limit={case_limit}; candidate_size={candidate_size}; "
        f"result_size={result_size}; arm={arm}: {designs[arm]}. "
        "Model choices are held fixed; only AI-Shop Search design is ablated."
    )


def _fingerprint(
    arm: str,
    *,
    dense_sha256: str,
    candidate_size: int,
    result_size: int,
) -> str:
    settings = get_settings()
    sources = {
        name: sha256_file(AGENT_ROOT / relative)
        for name, relative in {
            "pipeline": "app/services/product_search_pipeline.py",
            "queryPlan": "app/services/product_search_query.py",
            "reranker": "app/rag/retriever.py",
            "runner": "evaluation/emcr_project_transfer.py",
        }.items()
    }
    return sha256_bytes(
        canonical_json_bytes(
            {
                "arm": arm,
                "candidateSize": candidate_size,
                "denseRankingSha256": dense_sha256,
                "rerankApiFormat": settings.rerank_api_format,
                "rerankInstructionSha256": sha256_bytes(settings.rerank_instruct.encode("utf-8")),
                "rerankModel": settings.rerank_model,
                "resultSize": result_size,
                "runnerVersion": RUNNER_VERSION,
                "sources": sources,
            }
        )
    )


def _manifest(
    arm: str,
    rows: list[dict[str, Any]],
    *,
    normalized_path: Path,
    inventory_sha256: str,
    fingerprint: str,
    case_limit: int,
    candidate_size: int,
    result_size: int,
) -> dict[str, Any]:
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "datasetId": f"emcr-project-search-adapted-{arm.replace('_', '-')}",
        "officialUrl": OFFICIAL_URL,
        "license": "Apache-2.0",
        "upstreamRevisionOrCommit": UPSTREAM_REVISION,
        "perFileInventoryOrCanonicalInventorySha256": inventory_sha256,
        "selectionPolicy": _selection_policy(
            arm,
            case_limit=case_limit,
            candidate_size=candidate_size,
            result_size=result_size,
        ),
        "scorerVersion": SCORER_VERSION,
        "modelAndPromptFingerprintOrNOT_APPLICABLE": fingerprint,
        "caseCountAndEligibleDenominators": _denominators(rows),
        "normalizedInputSha256": sha256_file(normalized_path),
        "exhaustiveClaimGold": False,
        "exhaustiveCitationGold": False,
        "officialAgentExecution": False,
        **GOVERNANCE,
    }


def _validate_resume(
    rows: list[dict[str, Any]],
    selected: list[str],
    source_rows: dict[str, dict[str, Any]],
) -> None:
    if [row.get("caseKey") for row in rows] != selected[: len(rows)]:
        raise EmcrProjectTransferError("resume rows are not the deterministic selected prefix")
    for row in rows:
        source = source_rows[str(row["caseKey"])]
        if (
            set(row)
            != {
                "kind",
                "caseKey",
                "slice",
                "ranking",
                "qrels",
                "relevanceThreshold",
            }
            or row["kind"] != "ranking_case"
            or row["slice"] != source["slice"]
            or row["qrels"] != source["qrels"]
            or not isinstance(row["ranking"], list)
            or len(row["ranking"]) != len(set(row["ranking"]))
        ):
            raise EmcrProjectTransferError("resume rows do not match the frozen public inputs")


async def _run_arm(
    arm: str,
    *,
    case: dict[str, Any],
    bm25: dict[str, Any],
    dense: dict[str, Any],
    products: dict[str, dict[str, Any]],
    candidate_size: int,
    result_size: int,
) -> list[str]:
    plan = build_product_query_plan(case["text"], None)
    if arm == "no_constraint_guard":
        # A blank trusted surface disables only the evaluation-time guard while
        # preserving the real query for recall and reranking.
        plan = replace(
            plan,
            constraints=ProductRuntimeConstraints(),
            constraint_query=" ",
        )

    async def keyword_search(_variant: str, limit: int) -> list[str]:
        return [] if arm == "no_fusion" else list(bm25["ranking"][:limit])

    async def vector_search(_variant: str, limit: int) -> list[str]:
        return list(dense["ranking"][:limit])

    async def load_products(identifiers: list[str]) -> list[dict[str, Any]]:
        return [dict(products[value]) for value in identifiers]

    result = await product_search_pipeline.search(
        plan,
        candidate_size=candidate_size,
        result_size=result_size,
        keyword_search=keyword_search,
        vector_search=vector_search,
        load_products=load_products,
        rerank=rag_retriever.rerank_products,
        deadline_seconds=120.0,
        provider_timeout_seconds=30.0,
    )
    if result.trace.provider_calls.get("rerank") and result.trace.fallback:
        raise EmcrProjectTransferError("fixed reranker fell back; refusing mixed-arm evidence")
    ranking = [
        str(product.get("product_id") or product.get("productId") or product.get("id") or "")
        for product in result.products
    ]
    ranking = [value for value in ranking if value]
    if len(ranking) != len(set(ranking)):
        raise EmcrProjectTransferError("production pipeline returned duplicate product IDs")
    return ranking


async def run(
    *,
    raw_path: Path,
    bm25_path: Path,
    dense_path: Path,
    output: Path,
    case_limit: int = 20,
    candidate_size: int = 50,
    result_size: int = 10,
    resume: bool = False,
) -> dict[str, int]:
    if case_limit < 1 or candidate_size < 1 or not 1 <= result_size <= candidate_size:
        raise EmcrProjectTransferError(
            "limits require cases/candidates >= 1 and results <= candidates"
        )
    bm25_rows = _load_rankings(bm25_path)
    dense_rows = _load_rankings(dense_path)
    if set(bm25_rows) != set(dense_rows):
        raise EmcrProjectTransferError("BM25 and dense inputs contain different case sets")
    for key in bm25_rows:
        if (
            bm25_rows[key]["slice"] != dense_rows[key]["slice"]
            or bm25_rows[key]["qrels"] != dense_rows[key]["qrels"]
        ):
            raise EmcrProjectTransferError("BM25 and dense inputs disagree on public gold")
    selected = sorted(bm25_rows)[:case_limit]
    needed_ids = {
        value
        for key in selected
        for row in (bm25_rows[key], dense_rows[key])
        for value in row["ranking"][:candidate_size]
    }
    cases, products = _load_projection(
        raw_path,
        selected_keys=set(selected),
        needed_ids=needed_ids,
    )
    for key in selected:
        if (
            cases[key]["slice"] != bm25_rows[key]["slice"]
            or cases[key]["qrels"] != bm25_rows[key]["qrels"]
        ):
            raise EmcrProjectTransferError("raw and normalized public gold disagree")

    inventory_sha256 = sha256_bytes(
        canonical_json_bytes(
            {
                "bm25": sha256_file(bm25_path),
                "dense": sha256_file(dense_path),
                "raw": sha256_file(raw_path),
            }
        )
    )
    dense_sha256 = sha256_file(dense_path)
    output.mkdir(parents=True, exist_ok=True)
    allowed = {
        f"{arm}.{suffix}" for arm in ARMS for suffix in ("normalized.jsonl", "manifest.json")
    }
    existing_names = {path.name for path in output.iterdir()}
    if existing_names - allowed or (existing_names and not resume):
        raise EmcrProjectTransferError("output is non-empty; use --resume for this run only")

    arm_rows: dict[str, list[dict[str, Any]]] = {}
    arm_paths: dict[str, tuple[Path, Path]] = {}
    fingerprints = {
        arm: _fingerprint(
            arm,
            dense_sha256=dense_sha256,
            candidate_size=candidate_size,
            result_size=result_size,
        )
        for arm in ARMS
    }
    for arm in ARMS:
        normalized_path = output / f"{arm}.normalized.jsonl"
        manifest_path = output / f"{arm}.manifest.json"
        if normalized_path.exists():
            rows = load_jsonl(normalized_path)
            _validate_resume(rows, selected, bm25_rows)
        else:
            rows = []
        if (
            manifest_path.exists()
            and load_json(manifest_path).get("modelAndPromptFingerprintOrNOT_APPLICABLE")
            != fingerprints[arm]
        ):
            raise EmcrProjectTransferError("resume manifest has a different runtime fingerprint")
        arm_rows[arm] = rows
        arm_paths[arm] = (normalized_path, manifest_path)

    for index, key in enumerate(selected):
        for arm in ARMS:
            if index < len(arm_rows[arm]):
                continue
            ranking = await _run_arm(
                arm,
                case=cases[key],
                bm25=bm25_rows[key],
                dense=dense_rows[key],
                products=products,
                candidate_size=candidate_size,
                result_size=result_size,
            )
            source = bm25_rows[key]
            row = {
                "kind": "ranking_case",
                "caseKey": key,
                "slice": source["slice"],
                "ranking": ranking,
                "qrels": source["qrels"],
                "relevanceThreshold": int(source.get("relevanceThreshold", 3)),
            }
            normalized_path, manifest_path = arm_paths[arm]
            with normalized_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            arm_rows[arm].append(row)
    for arm in ARMS:
        normalized_path, manifest_path = arm_paths[arm]
        atomic_write_json(
            manifest_path,
            _manifest(
                arm,
                arm_rows[arm],
                normalized_path=normalized_path,
                inventory_sha256=inventory_sha256,
                fingerprint=fingerprints[arm],
                case_limit=case_limit,
                candidate_size=candidate_size,
                result_size=result_size,
            ),
        )
    return {arm: len(arm_rows[arm]) for arm in ARMS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--bm25", type=Path, required=True)
    parser.add_argument("--dense", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-limit", type=int, default=20)
    parser.add_argument("--candidate-size", type=int, default=50)
    parser.add_argument("--result-size", type=int, default=10)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    counts = asyncio.run(
        run(
            raw_path=args.raw,
            bm25_path=args.bm25,
            dense_path=args.dense,
            output=args.output,
            case_limit=args.case_limit,
            candidate_size=args.candidate_size,
            result_size=args.result_size,
            resume=args.resume,
        )
    )
    print(json.dumps({"armCounts": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
