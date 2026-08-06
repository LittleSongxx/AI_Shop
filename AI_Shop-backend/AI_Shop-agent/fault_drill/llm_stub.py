from __future__ import annotations

import hashlib
import json

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI(title="AI Shop deterministic LLM stub")


@app.get("/health")
async def health() -> dict:
    return {"status": "UP", "deterministic": True}


def _completion(model: str) -> dict:
    return {
        "id": "chatcmpl-fault-drill",
        "object": "chat.completion",
        "created": 1_786_000_000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "fault-drill deterministic response",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 4, "total_tokens": 8},
    }


@app.post("/v1/chat/completions")
async def chat_completions(body: dict):
    model = str(body.get("model") or "fault-drill-model")
    if not body.get("stream"):
        return _completion(model)

    async def chunks():
        first = {
            "id": "chatcmpl-fault-drill",
            "object": "chat.completion.chunk",
            "created": 1_786_000_000,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": "fault-drill deterministic response",
                    },
                    "finish_reason": None,
                }
            ],
        }
        last = {
            "id": "chatcmpl-fault-drill",
            "object": "chat.completion.chunk",
            "created": 1_786_000_000,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(first, separators=(',', ':'))}\n\n"
        yield f"data: {json.dumps(last, separators=(',', ':'))}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(chunks(), media_type="text/event-stream")


@app.post("/v1/embeddings")
async def embeddings(body: dict) -> dict:
    raw = body.get("input")
    values = raw if isinstance(raw, list) else [raw]
    dimensions = max(1, min(int(body.get("dimensions") or 16), 1024))
    data = []
    for index, value in enumerate(values):
        digest = hashlib.sha256(str(value or "").encode("utf-8")).digest()
        vector = [((digest[i % len(digest)] / 255.0) * 2) - 1 for i in range(dimensions)]
        data.append({"object": "embedding", "index": index, "embedding": vector})
    return {
        "object": "list",
        "data": data,
        "model": str(body.get("model") or "fault-drill-embedding"),
        "usage": {"prompt_tokens": len(values), "total_tokens": len(values)},
        "created": 1_786_000_000,
    }
