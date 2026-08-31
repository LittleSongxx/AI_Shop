from __future__ import annotations

import asyncio

from app.rag.prompt_builder import RAG_REFUSAL_TEXT
from evaluation import rgb_rag_transfer


def _has_forbidden_key(value: object) -> bool:
    if isinstance(value, dict):
        return bool(rgb_rag_transfer._FORBIDDEN_KEYS.intersection(value)) or any(
            _has_forbidden_key(child) for child in value.values()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(child) for child in value)
    return False


def test_rgb_supplied_evidence_rows_use_generation_and_verifier(monkeypatch) -> None:
    async def generated(_query: str, retrieval: dict) -> tuple[str, dict]:
        assert retrieval["trace"]["mode"] == "public_supplied_evidence"
        return "答案是北京 [1]。", {"repairRemaining": None}

    monkeypatch.setattr(rgb_rag_transfer, "_generate", generated)
    row = {
        "id": 7,
        "query": "公开合成问题",
        "answer": ["北京"],
        "positive": ["答案是北京。"],
        "negative": ["无关文档一。", "无关文档二。"],
    }

    first = asyncio.run(
        rgb_rag_transfer._case_rows(
            row,
            mode="refine",
            passage_count=2,
            noise_rate=0.5,
            seed=2333,
        )
    )
    second = asyncio.run(
        rgb_rag_transfer._case_rows(
            row,
            mode="refine",
            passage_count=2,
            noise_rate=0.5,
            seed=2333,
        )
    )

    assert first == second
    assert first[0]["task"] == "answer_groups"
    assert first[1]["task"] == "binary_classification"
    assert first[1]["predictedPositive"] is True
    assert not _has_forbidden_key(first)


def test_rgb_refusal_uses_project_exact_abstention(monkeypatch) -> None:
    async def generated(_query: str, retrieval: dict) -> tuple[str, dict]:
        assert retrieval["evidenceState"] == "INSUFFICIENT"
        return RAG_REFUSAL_TEXT, {"repairRemaining": None}

    monkeypatch.setattr(rgb_rag_transfer, "_generate", generated)
    rows = asyncio.run(
        rgb_rag_transfer._case_rows(
            {
                "id": 8,
                "query": "公开合成问题",
                "answer": ["不会使用"],
                "positive": ["不会被选中"],
                "negative": ["无关文档一。", "无关文档二。"],
            },
            mode="refusal",
            passage_count=2,
            noise_rate=1.0,
            seed=2333,
        )
    )

    assert rows[0]["task"] == "exact_or_alias"
    assert rows[0]["goldAnswers"] == [RAG_REFUSAL_TEXT]
    assert rows[1]["predictedPositive"] is True
    assert not _has_forbidden_key(rows)


def test_rgb_integration_keeps_one_document_per_answer_group() -> None:
    documents = rgb_rag_transfer._select_documents(
        {
            "positive": [["p1a", "p1b"], ["p2a", "p2b"]],
            "negative": ["n1", "n2", "n3"],
        },
        mode="integration",
        passage_count=5,
        noise_rate=0.6,
        seed=2333,
    )

    assert len(documents) == 5
    assert sum(document.startswith("p") for document in documents) == 2
    assert sum(document.startswith("n") for document in documents) == 3
