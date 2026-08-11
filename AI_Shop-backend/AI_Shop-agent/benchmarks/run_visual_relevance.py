from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.visual_eval import (  # noqa: E402
    LOCK_PATH,
    build_query_image,
    evaluate_predictions,
    gate_failures,
    load_cases,
    validate_contract,
)


async def _retrieve(case) -> dict[str, Any]:
    from app.services.product_service import product_service
    from app.visual.image_processing import normalize_query_image
    from app.visual.index import visual_product_index
    from app.visual.provider import visual_provider
    from app.visual.search_service import VisualProductSearchService, _weighted_rrf

    raw = build_query_image(case)
    query_image = normalize_query_image(raw)
    exact, embedding = await asyncio.gather(
        visual_product_index.exact_hash_hits(
            [hashlib.sha256(raw).hexdigest(), query_image.sha256]
        ),
        visual_provider.embed_image(query_image.data_uri),
    )
    image_hits, fused_hits = await asyncio.gather(
        visual_product_index.search_knn(
            embedding.vector,
            document_type="IMAGE",
            size=60,
        ),
        visual_product_index.search_knn(
            embedding.vector,
            document_type="PRODUCT_FUSED",
            size=40,
        ),
    )
    merged, _trace = _weighted_rrf(
        exact,
        image_hits,
        fused_hits,
        [],
        min_cosine=__import__("app.config.settings", fromlist=["get_settings"])
        .get_settings()
        .visual_embedding_min_cosine,
    )
    if not merged:
        return {"rankedProductIds": [], "unavailableProductIds": []}
    exact_ids = {hit.product_id for hit in exact}
    reranked, _rerank_trace = await VisualProductSearchService()._rerank(
        query_image, merged[:12]
    )
    reranked = [
        *(hit for hit in reranked if hit.product_id in exact_ids),
        *(hit for hit in reranked if hit.product_id not in exact_ids),
    ]
    candidate_ids = list(dict.fromkeys(hit.product_id for hit in reranked))
    products = await product_service.load_verified_products(candidate_ids)
    available_ids = {str(product.get("product_id") or "") for product in products}
    ranked_ids = [product_id for product_id in candidate_ids if product_id in available_ids]
    return {
        "rankedProductIds": ranked_ids[:5],
        "unavailableProductIds": [],
    }


async def run_live(limit: int | None = None) -> dict[str, Any]:
    from app.infra.http_client import close_clients
    from app.visual.index import visual_product_index

    cases = load_cases()
    contract = validate_contract(cases)
    status = await visual_product_index.status()
    if not status.get("servingCurrentModel"):
        raise RuntimeError(f"visual index is not serving the locked model: {status}")
    selected = cases[:limit] if limit else cases
    predictions: dict[str, dict[str, Any]] = {}
    try:
        for case in selected:
            predictions[case.case_id] = await _retrieve(case)
    finally:
        await close_clients()
    report = evaluate_predictions(selected, predictions)
    return {"contract": contract, "index": status, "report": report, "predictions": predictions}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--fallback-outcomes", type=Path)
    args = parser.parse_args()
    cases = load_cases()
    contract = validate_contract(cases)
    if not args.live and not args.predictions:
        print(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.live:
        result = asyncio.run(run_live(args.limit))
        report = result["report"]
    else:
        predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
        fallback = (
            json.loads(args.fallback_outcomes.read_text(encoding="utf-8"))
            if args.fallback_outcomes
            else None
        )
        report = evaluate_predictions(cases, predictions, fallback_outcomes=fallback)
        result = {"contract": contract, "report": report}
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    failures = gate_failures(report, lock["thresholds"])
    result["gateFailures"] = failures
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
