from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from app.rag.retriever import rag_retriever
from evaluation.emcr_project_transfer import ARMS, run
from evaluation.public_transfer import run_import


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_project_design_arms_are_deterministic_resumable_and_public_transfer_safe(
    tmp_path: Path, monkeypatch
) -> None:
    text = "预算100元以内的手机"
    key = hashlib.sha256(text.encode()).hexdigest()
    raw = tmp_path / "raw.jsonl"
    bm25 = tmp_path / "bm25.jsonl"
    dense = tmp_path / "dense.jsonl"
    output = tmp_path / "output"
    products = [
        ("a", "A手机", 80, 3),
        ("b", "B手机", 90, 2),
        ("x", "X手机", 200, 0),
    ]
    _write_jsonl(
        raw,
        [
            {
                "content_id": product_id,
                "query": text,
                "task_type": "expand_price",
                "score": score,
                "title": title,
                "detail_ocr": "",
                "commodity_name": title,
                "std_brand_name": "",
                "cate_full_name": "手机",
                "reserve_price": price,
                "property_kvs": "{}",
            }
            for product_id, title, price, score in products
        ],
    )
    qrels = {product_id: score for product_id, _title, _price, score in products}
    common = {
        "kind": "ranking_case",
        "caseKey": key,
        "slice": "expand_price",
        "qrels": qrels,
        "relevanceThreshold": 3,
    }
    _write_jsonl(bm25, [{**common, "ranking": ["a", "b", "x"]}])
    _write_jsonl(dense, [{**common, "ranking": ["b", "a", "x"]}])

    async def fixed_reranker(_text: str, candidates: list[dict], limit: int) -> list[dict]:
        for candidate in candidates:
            candidate["_search_rerank_source"] = "rerank"
        return candidates[:limit]

    monkeypatch.setattr(rag_retriever, "rerank_products", fixed_reranker)
    counts = asyncio.run(
        run(
            raw_path=raw,
            bm25_path=bm25,
            dense_path=dense,
            output=output,
            case_limit=1,
            candidate_size=3,
            result_size=3,
        )
    )
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    resumed = asyncio.run(
        run(
            raw_path=raw,
            bm25_path=bm25,
            dense_path=dense,
            output=output,
            case_limit=1,
            candidate_size=3,
            result_size=3,
            resume=True,
        )
    )

    assert counts == resumed == {arm: 1 for arm in ARMS}
    assert before == {path.name: path.read_bytes() for path in output.iterdir()}
    rankings = {
        arm: json.loads((output / f"{arm}.normalized.jsonl").read_text())["ranking"] for arm in ARMS
    }
    assert rankings == {
        "full": ["a", "b"],
        "no_fusion": ["b", "a"],
        "no_constraint_guard": ["a", "b", "x"],
    }
    for arm in ARMS:
        manifest = json.loads((output / f"{arm}.manifest.json").read_text())
        assert "PROJECT_SEARCH_ADAPTED_TRANSFER" in manifest["selectionPolicy"]
        assert "not full-stack" in manifest["selectionPolicy"]
        run_import(
            manifest_path=output / f"{arm}.manifest.json",
            input_path=output / f"{arm}.normalized.jsonl",
            output=tmp_path / f"report-{arm}",
        )
