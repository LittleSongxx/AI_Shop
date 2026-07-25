"""Run a deliberately manual smoke check against the configured AI providers."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


async def run() -> dict[str, object]:
    from app.config.settings import get_settings
    from app.rag.embedding import embed_text
    from app.rag.retriever import RagRetriever
    from app.services.llm_factory import create_chat_llm

    settings = get_settings()
    missing = [
        name
        for name, value in (
            ("LLM_API_KEY", settings.llm_api_key),
            ("EMBEDDING_API_KEY", settings.embedding_api_key),
            ("RERANK_API_KEY", settings.rerank_api_key),
        )
        if not value.strip()
    ]
    if missing:
        raise RuntimeError("missing provider credentials: " + ", ".join(missing))

    llm = create_chat_llm()
    response = await llm.ainvoke("Reply with the single word OK.")
    llm_text = str(getattr(response, "content", "") or "").strip()
    if not llm_text:
        raise RuntimeError("LLM returned an empty response")

    vector = await embed_text("AI_Shop 在线模型检查")
    if not vector or len(vector) != settings.embedding_dimensions:
        raise RuntimeError(
            "Embedding dimension mismatch: "
            f"expected {settings.embedding_dimensions}, got {len(vector) if vector else 0}"
        )

    reranked = await RagRetriever()._rerank(
        "适合办公的轻薄笔记本",
        [
            {
                "id": "model-check",
                "content": "轻薄笔记本，适合办公和移动使用",
                "metadata": {},
                "score": 0.1,
            }
        ],
        1,
    )
    if not reranked:
        raise RuntimeError("Rerank returned no result")

    return {
        "llm": "ok",
        "embedding": {"status": "ok", "dimensions": len(vector)},
        "rerank": {"status": "ok", "count": len(reranked)},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the live AI providers.")
    parser.parse_args()
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
