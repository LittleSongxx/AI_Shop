"""Run a deliberately manual smoke check against the configured AI providers."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Collection
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


COMPONENTS = ("llm", "embedding", "rerank")


async def run(components: Collection[str] | None = None) -> dict[str, object]:
    from app.config.settings import get_settings
    from app.rag.embedding import embed_text
    from app.rag.retriever import RagRetriever
    from app.services.llm_factory import create_chat_llm

    selected = set(components or COMPONENTS)
    unknown = selected.difference(COMPONENTS)
    if unknown:
        raise ValueError("unknown components: " + ", ".join(sorted(unknown)))

    settings = get_settings()
    credentials = {
        "llm": ("LLM_API_KEY", settings.llm_api_key),
        "embedding": ("EMBEDDING_API_KEY", settings.embedding_api_key),
        "rerank": ("RERANK_API_KEY", settings.rerank_api_key),
    }
    missing = [
        credentials[component][0]
        for component in COMPONENTS
        if component in selected
        for value in (credentials[component][1],)
        if not value.strip()
    ]
    if missing:
        raise RuntimeError("missing provider credentials: " + ", ".join(missing))

    result: dict[str, object] = {}
    if "llm" in selected:
        llm = create_chat_llm()
        response = await llm.ainvoke("Reply with the single word OK.")
        llm_text = str(getattr(response, "content", "") or "").strip()
        if not llm_text:
            raise RuntimeError("LLM returned an empty response")
        result["llm"] = "ok"

    if "embedding" in selected:
        vector = await embed_text("AI_Shop 在线模型检查")
        if not vector or len(vector) != settings.embedding_dimensions:
            raise RuntimeError(
                "Embedding dimension mismatch: "
                f"expected {settings.embedding_dimensions}, got {len(vector) if vector else 0}"
            )
        result["embedding"] = {"status": "ok", "dimensions": len(vector)}

    if "rerank" in selected:
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
        if not reranked or reranked[0].get("source") != "rerank":
            raise RuntimeError("Rerank request did not complete; the retriever used its fallback")
        result["rerank"] = {"status": "ok", "count": len(reranked)}

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the live AI providers.")
    parser.add_argument(
        "--component",
        choices=("all", *COMPONENTS),
        default="all",
        help="provider to test (default: all)",
    )
    args = parser.parse_args()
    selected = COMPONENTS if args.component == "all" else (args.component,)
    print(json.dumps(asyncio.run(run(selected)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
