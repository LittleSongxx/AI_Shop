from __future__ import annotations

import asyncio
import json
import math
import random
import re
from urllib.parse import urlparse

import httpx
import structlog

from app.config.settings import get_settings
from app.harness.agents.contracts import VisualSubject
from app.infra.http_client import get_client
from app.resilience.circuit_breaker import CircuitState, circuit_registry
from app.visual.contracts import (
    GroundingResult,
    VisualEmbeddingResult,
    VisualProviderError,
    VisualProviderMetadata,
    VisualRerankItem,
    VisualRerankResult,
)

logger = structlog.get_logger()

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.I | re.S)


class VisualProvider:
    async def locate_subjects(self, image_data_uri: str) -> GroundingResult:
        settings = get_settings()
        body = {
            "model": settings.visual_grounding_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Locate purchasable product entities only. Return strict JSON as "
                        '{"subjects":[{"label":"short Chinese noun","bbox":[x1,y1,x2,y2]}]}. '
                        "Coordinates are integers in [0,999]. Return at most 5 non-overlapping "
                        "subjects. Ignore people, body parts, background, text and packaging noise."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_data_uri}},
                        {"type": "text", "text": "定位图中可购买的商品主体。"},
                    ],
                },
            ],
            "temperature": 0,
            "max_tokens": 500,
            "response_format": {"type": "json_object"},
        }
        payload, attempts, state = await self._post_with_resilience(
            "grounding",
            f"{settings.visual_grounding_base_url.rstrip('/')}/chat/completions",
            body,
            api_key=settings.visual_api_key,
            timeout=settings.visual_grounding_timeout_seconds,
        )
        subjects = _parse_grounding_payload(payload, settings.visual_max_subjects)
        return GroundingResult(
            subjects=subjects,
            metadata=VisualProviderMetadata(
                capability="grounding",
                model=settings.visual_grounding_model,
                request_id=_request_id(payload),
                usage=_usage(payload),
                circuit_state=state,
                attempts=attempts,
            ),
        )

    async def describe_product_attributes(self, image_data_uri: str) -> tuple[str, dict]:
        settings = get_settings()
        body = {
            "model": settings.visual_grounding_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Describe only visible shopping attributes as strict JSON with keys "
                        "category,color,material,shape,brand,keywords. Values must be short; "
                        "use null or [] when not visible. Never infer price or authenticity."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_data_uri}},
                        {"type": "text", "text": "提取可用于商城检索的可见属性。"},
                    ],
                },
            ],
            "temperature": 0,
            "max_tokens": 300,
            "response_format": {"type": "json_object"},
        }
        payload, _attempts, _state = await self._post_with_resilience(
            "grounding",
            f"{settings.visual_grounding_base_url.rstrip('/')}/chat/completions",
            body,
            api_key=settings.visual_api_key,
            timeout=settings.visual_grounding_timeout_seconds,
        )
        raw = _extract_chat_json(payload)
        allowed: list[str] = []
        for key in ("category", "color", "material", "shape", "brand"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                allowed.append(value.strip()[:32])
        keywords = raw.get("keywords")
        if isinstance(keywords, list):
            allowed.extend(str(item).strip()[:24] for item in keywords[:5] if str(item).strip())
        return " ".join(dict.fromkeys(allowed))[:180], raw

    async def embed_image(
        self, image_data_uri: str, *, text: str | None = None
    ) -> VisualEmbeddingResult:
        settings = get_settings()
        contents: list[dict[str, str]] = []
        cleaned_text = (text or "").strip()
        if cleaned_text:
            contents.append({"text": cleaned_text[:1000]})
        contents.append({"image": image_data_uri})
        body = {
            "model": settings.visual_embedding_model,
            "input": {"contents": contents},
            "parameters": {
                "enable_fusion": bool(cleaned_text),
                "dimension": settings.visual_embedding_dimensions,
                "instruct": "Retrieve visually similar purchasable products.",
            },
        }
        payload, attempts, state = await self._post_with_resilience(
            "embedding",
            settings.visual_embedding_url,
            body,
            api_key=settings.visual_api_key,
            timeout=settings.visual_embedding_timeout_seconds,
        )
        vector = _embedding_vector(payload, settings.visual_embedding_dimensions)
        return VisualEmbeddingResult(
            vector=vector,
            metadata=VisualProviderMetadata(
                capability="embedding",
                model=settings.visual_embedding_model,
                request_id=_request_id(payload),
                usage=_usage(payload),
                circuit_state=state,
                attempts=attempts,
            ),
        )

    async def embed_product(
        self, text: str, image_data_uris: list[str]
    ) -> VisualEmbeddingResult:
        settings = get_settings()
        images = [item for item in image_data_uris if item][:5]
        if not images:
            raise VisualProviderError("VISUAL_PRODUCT_IMAGE_REQUIRED")
        contents: list[dict[str, str]] = [{"text": (text or "商品")[:2000]}]
        contents.extend({"image": item} for item in images)
        body = {
            "model": settings.visual_embedding_model,
            "input": {"contents": contents},
            "parameters": {
                "enable_fusion": True,
                "dimension": settings.visual_embedding_dimensions,
                "instruct": "Represent this e-commerce product for visual retrieval.",
            },
        }
        payload, attempts, state = await self._post_with_resilience(
            "embedding",
            settings.visual_embedding_url,
            body,
            api_key=settings.visual_api_key,
            timeout=settings.visual_embedding_timeout_seconds,
        )
        return VisualEmbeddingResult(
            vector=_embedding_vector(payload, settings.visual_embedding_dimensions),
            metadata=VisualProviderMetadata(
                capability="embedding",
                model=settings.visual_embedding_model,
                request_id=_request_id(payload),
                usage=_usage(payload),
                circuit_state=state,
                attempts=attempts,
            ),
        )

    async def rerank(
        self, query_image_data_uri: str, document_image_data_uris: list[str]
    ) -> VisualRerankResult:
        settings = get_settings()
        documents = [item for item in document_image_data_uris if item][:40]
        if not documents:
            raise VisualProviderError("VISUAL_RERANK_DOCUMENTS_REQUIRED")
        body = {
            "model": settings.visual_rerank_model,
            "input": {
                "query": {"image": query_image_data_uri},
                "documents": [{"image": item} for item in documents],
            },
            "parameters": {
                "return_documents": False,
                "top_n": len(documents),
                "instruct": "Rank product images by visual similarity to the query product.",
            },
        }
        payload, attempts, state = await self._post_with_resilience(
            "rerank",
            self._visual_rerank_url(),
            body,
            api_key=settings.visual_rerank_api_key,
            timeout=settings.visual_rerank_timeout_seconds,
        )
        items = _rerank_items(payload, len(documents))
        return VisualRerankResult(
            items=items,
            metadata=VisualProviderMetadata(
                capability="rerank",
                model=settings.visual_rerank_model,
                request_id=_request_id(payload),
                usage=_usage(payload),
                circuit_state=state,
                attempts=attempts,
            ),
        )

    def _visual_rerank_url(self) -> str:
        settings = get_settings()
        if settings.visual_rerank_base_url.strip():
            return settings.visual_rerank_base_url.strip()
        base = settings.rerank_base_url.strip()
        if not base:
            raise VisualProviderError("VISUAL_RERANK_NOT_CONFIGURED")
        parsed = urlparse(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise VisualProviderError("VISUAL_RERANK_URL_INVALID")
        return (
            f"{parsed.scheme}://{parsed.netloc}"
            "/api/v1/services/rerank/text-rerank/text-rerank"
        )

    async def _post_with_resilience(
        self,
        capability: str,
        url: str,
        body: dict,
        *,
        api_key: str,
        timeout: float,
    ) -> tuple[dict, int, str]:
        if not api_key.strip():
            raise VisualProviderError(f"VISUAL_{capability.upper()}_NOT_CONFIGURED")
        breaker = circuit_registry.get_or_create(
            f"visual_{capability}", failure_threshold=5, recovery_timeout=60
        )
        if not breaker.allow_request():
            raise VisualProviderError(f"VISUAL_{capability.upper()}_CIRCUIT_OPEN", retryable=True)

        settings = get_settings()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.visual_provider_deadline_seconds
        last_error: Exception | None = None
        attempts = 0
        for attempt in range(2):
            attempts = attempt + 1
            remaining = deadline - loop.time()
            if remaining <= 0:
                last_error = TimeoutError("visual provider deadline exceeded")
                break
            try:
                client = await get_client(f"visual_{capability}", timeout=timeout)
                response = await client.post(
                    url,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=min(timeout, remaining),
                )
                if response.status_code in _RETRYABLE_STATUS:
                    raise _RetryableHttpError(response.status_code)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise VisualProviderError(f"VISUAL_{capability.upper()}_INVALID_RESPONSE")
                if payload.get("code"):
                    raise VisualProviderError(f"VISUAL_{capability.upper()}_PROVIDER_REJECTED")
                breaker.record_success()
                return payload, attempts, CircuitState.CLOSED.value
            except (httpx.TimeoutException, httpx.NetworkError, _RetryableHttpError) as exc:
                last_error = exc
                if attempt == 0:
                    delay = random.uniform(0.08, 0.22)
                    if loop.time() + delay < deadline:
                        await asyncio.sleep(delay)
                        continue
                break
            except (httpx.HTTPStatusError, json.JSONDecodeError, VisualProviderError) as exc:
                breaker.release_probe()
                if isinstance(exc, VisualProviderError):
                    raise
                raise VisualProviderError(
                    f"VISUAL_{capability.upper()}_REQUEST_REJECTED"
                ) from exc

        breaker.record_failure()
        logger.warning(
            "visual_provider_failed",
            capability=capability,
            attempts=attempts,
            error=type(last_error).__name__ if last_error else "deadline",
            circuit_state=breaker.state.value,
        )
        raise VisualProviderError(
            f"VISUAL_{capability.upper()}_TEMPORARILY_UNAVAILABLE", retryable=True
        ) from last_error


class _RetryableHttpError(RuntimeError):
    def __init__(self, status_code: int):
        super().__init__(f"retryable status {status_code}")
        self.status_code = status_code


def _extract_chat_json(payload: dict) -> dict:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VisualProviderError("VISUAL_GROUNDING_INVALID_RESPONSE") from exc
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "") for item in content if isinstance(item, dict)
        )
    text = str(content or "").strip()
    fenced = _JSON_FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise VisualProviderError("VISUAL_GROUNDING_INVALID_JSON") from exc
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as nested:
            raise VisualProviderError("VISUAL_GROUNDING_INVALID_JSON") from nested
    if not isinstance(parsed, dict):
        raise VisualProviderError("VISUAL_GROUNDING_INVALID_JSON")
    return parsed


def _parse_grounding_payload(payload: dict, max_subjects: int) -> list[VisualSubject]:
    raw = _extract_chat_json(payload).get("subjects")
    if not isinstance(raw, list):
        return []
    accepted: list[VisualSubject] = []
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "商品").strip()[:64]
        bbox = item.get("bbox")
        if not label or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        try:
            coordinates = tuple(_strict_json_int(value) for value in bbox)
            subject = VisualSubject(
                subject_id=f"subject_{len(accepted) + 1}",
                label=label,
                bbox=coordinates,
            )
        except (TypeError, ValueError):
            continue
        area = (coordinates[2] - coordinates[0]) * (coordinates[3] - coordinates[1])
        if area < 5_000:
            continue
        if any(_bbox_iou(subject.bbox, existing.bbox) >= 0.85 for existing in accepted):
            continue
        accepted.append(subject)
        if len(accepted) >= max_subjects:
            break
    return accepted


def _bbox_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    if not intersection:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return intersection / (area_a + area_b - intersection)


def _embedding_vector(payload: dict, dimensions: int) -> list[float]:
    try:
        raw = payload["output"]["embeddings"][0]["embedding"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VisualProviderError("VISUAL_EMBEDDING_INVALID_RESPONSE") from exc
    if not isinstance(raw, list) or len(raw) != dimensions:
        raise VisualProviderError("VISUAL_EMBEDDING_DIMENSION_MISMATCH")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw):
        raise VisualProviderError("VISUAL_EMBEDDING_INVALID_VECTOR")
    vector = [float(value) for value in raw]
    if not all(math.isfinite(value) for value in vector):
        raise VisualProviderError("VISUAL_EMBEDDING_INVALID_VECTOR")
    return vector


def _rerank_items(payload: dict, document_count: int) -> list[VisualRerankItem]:
    raw = (payload.get("output") or {}).get("results")
    if not isinstance(raw, list):
        raise VisualProviderError("VISUAL_RERANK_INVALID_RESPONSE")
    items: list[VisualRerankItem] = []
    seen: set[int] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            index = _strict_json_int(item["index"])
            raw_score = item["relevance_score"]
            if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                raise ValueError("invalid rerank score")
            score = float(raw_score)
        except (KeyError, TypeError, ValueError):
            continue
        if index in seen or not 0 <= index < document_count or not math.isfinite(score):
            continue
        seen.add(index)
        items.append(VisualRerankItem(index=index, relevance_score=score))
    if not items:
        raise VisualProviderError("VISUAL_RERANK_EMPTY_RESPONSE")
    return items


def _strict_json_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected a JSON integer")
    return value


def _request_id(payload: dict) -> str | None:
    value = payload.get("request_id") or payload.get("id")
    return str(value) if value else None


def _usage(payload: dict) -> dict:
    value = payload.get("usage")
    return dict(value) if isinstance(value, dict) else {}


visual_provider = VisualProvider()
