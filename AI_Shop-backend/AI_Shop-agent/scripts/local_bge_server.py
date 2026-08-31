"""Serve pinned local BGE embedding and rerank models over existing HTTP contracts."""

from __future__ import annotations

import argparse
import io
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MAX_BODY_BYTES = 2_000_000
MAX_EMBEDDING_INPUTS = 64
MAX_DOCUMENTS = 100
MAX_TEXT_CHARS = 16_000


class RequestError(ValueError):
    pass


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RequestError(f"{name} must be a non-empty string")
    if len(value) > MAX_TEXT_CHARS:
        raise RequestError(f"{name} exceeds {MAX_TEXT_CHARS} characters")
    return value


def _embedding_request(payload: Any, dimensions: int) -> tuple[str, list[str]]:
    if not isinstance(payload, dict):
        raise RequestError("request must be a JSON object")
    requested_dimensions = payload.get("dimensions")
    if requested_dimensions is not None and requested_dimensions != dimensions:
        raise RequestError(f"dimensions must be {dimensions}")
    raw = payload.get("input")
    values = raw if isinstance(raw, list) else [raw]
    if not 1 <= len(values) <= MAX_EMBEDDING_INPUTS:
        raise RequestError(f"input count must be 1..{MAX_EMBEDDING_INPUTS}")
    return str(payload.get("model") or "bge-m3"), [
        _text(value, f"input[{index}]") for index, value in enumerate(values)
    ]


def _rerank_request(payload: Any) -> tuple[str, str, list[str], int]:
    if not isinstance(payload, dict):
        raise RequestError("request must be a JSON object")
    query = _text(payload.get("query"), "query")
    if payload.get("instruct") is not None:
        # BGE reranker v2-m3 is not trained for the Qwen-style instruction
        # field. Validate the compatibility field but keep the model query raw.
        _text(payload["instruct"], "instruct")
    raw_documents = payload.get("documents")
    if not isinstance(raw_documents, list) or not 1 <= len(raw_documents) <= MAX_DOCUMENTS:
        raise RequestError(f"documents count must be 1..{MAX_DOCUMENTS}")
    documents = [_text(value, f"documents[{index}]") for index, value in enumerate(raw_documents)]
    top_n = payload.get("top_n", len(documents))
    if isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= len(documents):
        raise RequestError("top_n must be between 1 and the document count")
    return str(payload.get("model") or "bge-reranker-v2-m3"), query, documents, top_n


class Models:
    def __init__(
        self,
        embedding_path: Path,
        reranker_path: Path,
        *,
        device: str,
        embedding_batch_size: int,
        reranker_batch_size: int,
    ) -> None:
        import torch
        from sentence_transformers import CrossEncoder, SentenceTransformer

        model_kwargs = {"dtype": torch.float16} if device.startswith("cuda") else {}
        self.embedding = SentenceTransformer(
            str(embedding_path), device=device, model_kwargs=model_kwargs
        )
        self.embedding.max_seq_length = 512
        self.reranker = CrossEncoder(
            str(reranker_path),
            device=device,
            model_kwargs=model_kwargs,
            max_length=512,
        )
        self.dimensions = int(self.embedding.get_sentence_embedding_dimension())
        self.embedding_batch_size = embedding_batch_size
        self.reranker_batch_size = reranker_batch_size
        # ponytail: one GPU lock is enough for this single-host evaluator; split
        # workers only when measured request concurrency requires it.
        self.lock = threading.Lock()

    def embed(self, texts: list[str]) -> list[list[float]]:
        with self.lock:
            values = self.embedding.encode(
                texts,
                batch_size=min(self.embedding_batch_size, len(texts)),
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        vectors = [[float(item) for item in row] for row in values]
        if any(
            len(vector) != self.dimensions or not all(math.isfinite(item) for item in vector)
            for vector in vectors
        ):
            raise RuntimeError("embedding model returned an invalid vector")
        return vectors

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[dict[str, Any]]:
        with self.lock:
            scores = self.reranker.predict(
                [(query, document) for document in documents],
                batch_size=min(self.reranker_batch_size, len(documents)),
                show_progress_bar=False,
            )
        ranked = [(index, float(score)) for index, score in enumerate(scores)]
        if not all(math.isfinite(score) for _index, score in ranked):
            raise RuntimeError("reranker returned a non-finite score")
        ranked.sort(key=lambda item: (-item[1], item[0]))
        return [
            {"index": index, "relevance_score": float(score)} for index, score in ranked[:top_n]
        ]


class Handler(BaseHTTPRequestHandler):
    server_version = "AIShopLocalBGE/1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    @property
    def models(self) -> Models:
        return self.server.models  # type: ignore[attr-defined, no-any-return]

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(
            payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _payload(self) -> Any:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            if self.headers.get("Transfer-Encoding", "").lower() != "chunked":
                raise RequestError("Content-Length or chunked encoding is required")
            body = self._chunked_body()
        else:
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise RequestError("invalid Content-Length") from exc
            if not 1 <= length <= MAX_BODY_BYTES:
                raise RequestError(f"request body must be 1..{MAX_BODY_BYTES} bytes")
            body = self.rfile.read(length)
        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RequestError("request body must be valid JSON") from exc

    def _chunked_body(self) -> bytes:
        body = bytearray()
        while True:
            size_line = self.rfile.readline(128)
            if not size_line.endswith(b"\r\n"):
                raise RequestError("invalid chunk header")
            try:
                size = int(size_line[:-2].split(b";", 1)[0], 16)
            except ValueError as exc:
                raise RequestError("invalid chunk size") from exc
            if size == 0:
                for _ in range(32):
                    trailer = self.rfile.readline(8192)
                    if trailer == b"\r\n":
                        return bytes(body)
                raise RequestError("too many chunk trailers")
            if size < 0 or len(body) + size > MAX_BODY_BYTES:
                raise RequestError(f"request body exceeds {MAX_BODY_BYTES} bytes")
            chunk = self.rfile.read(size)
            if len(chunk) != size or self.rfile.read(2) != b"\r\n":
                raise RequestError("truncated chunked body")
            body.extend(chunk)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._send(404, {"error": {"message": "not found"}})
            return
        self._send(200, {"status": "ok", "dimensions": self.models.dimensions})

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = self._payload()
            if self.path == "/v1/embeddings":
                model, texts = _embedding_request(payload, self.models.dimensions)
                vectors = self.models.embed(texts)
                self._send(
                    200,
                    {
                        "object": "list",
                        "model": model,
                        "data": [
                            {"object": "embedding", "index": index, "embedding": vector}
                            for index, vector in enumerate(vectors)
                        ],
                    },
                )
                return
            if self.path == "/v1/rerank":
                model, query, documents, top_n = _rerank_request(payload)
                self._send(
                    200,
                    {
                        "model": model,
                        "results": self.models.rerank(query, documents, top_n),
                    },
                )
                return
            self._send(404, {"error": {"message": "not found"}})
        except RequestError as exc:
            self._send(400, {"error": {"message": str(exc)}})
        except Exception:
            self._send(500, {"error": {"message": "local inference failed"}})


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], models: Models) -> None:
        super().__init__(address, Handler)
        self.models = models


def _self_check() -> None:
    assert _embedding_request({"input": ["商品", "售后"], "dimensions": 1024}, 1024)[1] == [
        "商品",
        "售后",
    ]
    assert _rerank_request({"query": "轻薄电脑", "documents": ["电脑", "手机"], "top_n": 1})[3] == 1
    assert (
        _rerank_request(
            {
                "query": "轻薄电脑",
                "documents": ["电脑"],
                "instruct": "按直接相关性排序",
            }
        )[1]
        == "轻薄电脑"
    )
    try:
        _embedding_request({"input": "x", "dimensions": 768}, 1024)
    except RequestError:
        pass
    else:
        raise AssertionError("dimension mismatch must fail")
    handler = object.__new__(Handler)
    handler.rfile = io.BytesIO(b"4\r\ntest\r\n3\r\ning\r\n0\r\n\r\n")
    assert handler._chunked_body() == b"testing"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embedding-model", type=Path)
    parser.add_argument("--reranker-model", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7060)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--embedding-batch-size", type=int, default=8)
    parser.add_argument("--reranker-batch-size", type=int, default=4)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    _self_check()
    if args.self_check:
        return 0
    if args.embedding_model is None or args.reranker_model is None:
        parser.error("--embedding-model and --reranker-model are required")
    if not args.embedding_model.is_dir() or not args.reranker_model.is_dir():
        parser.error("model paths must be existing directories")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be 1..65535")
    if args.embedding_batch_size < 1 or args.reranker_batch_size < 1:
        parser.error("batch sizes must be positive")
    models = Models(
        args.embedding_model,
        args.reranker_model,
        device=args.device,
        embedding_batch_size=args.embedding_batch_size,
        reranker_batch_size=args.reranker_batch_size,
    )
    server = Server((args.host, args.port), models)
    print(
        json.dumps(
            {
                "status": "ready",
                "host": args.host,
                "port": args.port,
                "dimensions": models.dimensions,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
