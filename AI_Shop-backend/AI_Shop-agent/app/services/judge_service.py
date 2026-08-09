from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from app.config.settings import get_settings
from app.harness.metrics.runtime_sensors import (
    JUDGE_DROPPED_TOTAL,
    JUDGE_EVALUATION_TOTAL,
    JUDGE_LATENCY,
    JUDGE_QUEUE_DEPTH,
    JUDGE_SCORE,
)
from app.observability.telemetry import get_tracer
from app.services.badcase_service import badcase_service
from app.services.episode_service import episode_service
from app.services.llm_factory import ChatLLMConfig, chat_llm_for_config

logger = structlog.get_logger()
_JSON_RE = re.compile(r"\{.*\}", re.S)
_DIMENSIONS = ("groundedness", "relevance", "completeness", "constraintCompliance")


@dataclass(frozen=True)
class JudgeRequest:
    run_id: str
    message_id: int
    user_text: str
    assistant: str
    intent: str | None
    tools_called: tuple[str, ...]
    source_refs: Any
    verifier_passed: bool


class JudgeService:
    _STOP = object()

    def __init__(self) -> None:
        self._queue: asyncio.Queue[JudgeRequest | object] | None = None
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._worker is not None:
            return
        settings = get_settings()
        if not settings.judge_model.strip():
            return
        if not (settings.judge_api_key or settings.llm_api_key).strip():
            logger.warning("judge_disabled_missing_api_key")
            return
        self._queue = asyncio.Queue(maxsize=settings.judge_queue_size)
        self._worker = asyncio.create_task(self._worker_loop(), name="agent-shadow-judge")

    async def close(self) -> None:
        queue = self._queue
        worker = self._worker
        if queue is None or worker is None:
            return
        try:
            queue.put_nowait(self._STOP)
        except asyncio.QueueFull:
            JUDGE_DROPPED_TOTAL.labels(reason="shutdown_queue_full").inc()
            worker.cancel()
        try:
            await asyncio.wait_for(worker, timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        finally:
            self._queue = None
            self._worker = None
            JUDGE_QUEUE_DEPTH.set(0)

    def enqueue(
        self,
        *,
        run_id: str | None,
        message_id: int,
        user_text: str | None,
        assistant: str,
        intent: str | None,
        tools_called: list[str] | None,
        source_refs: Any,
        verifier_passed: bool,
        force: bool = False,
    ) -> bool:
        queue = self._queue
        if queue is None or self._worker is None or not run_id:
            return False
        if not force and not self._sample(run_id, get_settings().judge_sample_rate):
            JUDGE_EVALUATION_TOTAL.labels(result="not_sampled").inc()
            return False
        request = JudgeRequest(
            run_id=str(run_id),
            message_id=int(message_id),
            user_text=str(user_text or "")[:4_000],
            assistant=str(assistant or "")[:8_000],
            intent=str(intent or "")[:40] or None,
            tools_called=tuple(str(tool)[:64] for tool in tools_called or []),
            source_refs=source_refs,
            verifier_passed=bool(verifier_passed),
        )
        try:
            queue.put_nowait(request)
            JUDGE_QUEUE_DEPTH.set(queue.qsize())
            JUDGE_EVALUATION_TOTAL.labels(result="queued").inc()
            return True
        except asyncio.QueueFull:
            JUDGE_DROPPED_TOTAL.labels(reason="queue_full").inc()
            return False

    async def _worker_loop(self) -> None:
        assert self._queue is not None
        queue = self._queue
        while True:
            item = await queue.get()
            if item is self._STOP:
                queue.task_done()
                break
            try:
                await self._evaluate(item)  # type: ignore[arg-type]
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                JUDGE_EVALUATION_TOTAL.labels(result="error").inc()
                logger.warning(
                    "shadow_judge_failed",
                    error=type(exc).__name__,
                )
            finally:
                queue.task_done()
                JUDGE_QUEUE_DEPTH.set(queue.qsize())

    async def _evaluate(self, request: JudgeRequest) -> None:
        started = time.perf_counter()
        with get_tracer().start_as_current_span("agent.shadow_judge") as span:
            span.set_attribute("agent.run_id", request.run_id)
            span.set_attribute("agent.message_id", request.message_id)
            response = await _judge_llm().ainvoke(
                [
                    SystemMessage(
                        content=(
                            "你是电商客服答案质量审核器。只依据给定问题、答案、工具名和引用元数据评分，"
                            "不得补充外部事实。返回单个 JSON 对象，字段 groundedness、relevance、"
                            "completeness、constraintCompliance 均为 0 到 1；reason 为不超过 120 字的"
                            "可复核理由。不要输出分析过程或 markdown。"
                        )
                    ),
                    HumanMessage(content=_judge_input(request)),
                ]
            )
            result = _parse_result(getattr(response, "content", response))
            elapsed = time.perf_counter() - started
            JUDGE_LATENCY.observe(elapsed)
            scores = {dimension: result[dimension] for dimension in _DIMENSIONS}
            for dimension, score in scores.items():
                JUDGE_SCORE.labels(dimension=dimension).observe(score)
            minimum = min(scores.values())
            low = minimum < get_settings().judge_low_score_threshold
            quality = {
                "judge": {
                    **scores,
                    "reason": result["reason"],
                    "lowScore": low,
                    "latencyMs": round(elapsed * 1_000),
                }
            }
            episode_service.update_run(run_id=request.run_id, quality=quality)
            episode_service.record_step(
                "ASYNC_JUDGE",
                run_id=request.run_id,
                node_name="judge",
                status="LOW_SCORE" if low else "OK",
                output_data=quality,
                model_name=get_settings().judge_model,
                latency_ms=round(elapsed * 1_000),
            )
            if low:
                await badcase_service.add_candidate(
                    request.message_id,
                    "JUDGE_LOW_SCORE",
                    result["reason"] or "异步 Judge 低分",
                    run_id=request.run_id,
                    source="JUDGE",
                    severity="MEDIUM",
                    snapshot={"minimumScore": minimum},
                    judge=quality["judge"],
                )
                JUDGE_EVALUATION_TOTAL.labels(result="low_score").inc()
            else:
                JUDGE_EVALUATION_TOTAL.labels(result="pass").inc()

    @staticmethod
    def _sample(run_id: str, rate: float) -> bool:
        if rate <= 0:
            return False
        if rate >= 1:
            return True
        bucket = int(hashlib.sha256(run_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        return bucket < rate


@lru_cache(maxsize=4)
def _judge_llm():
    settings = get_settings()
    return chat_llm_for_config(
        ChatLLMConfig(
            api_key=(settings.judge_api_key or settings.llm_api_key).strip(),
            base_url=(settings.judge_base_url or settings.llm_base_url).strip(),
            model=settings.judge_model.strip(),
            timeout=settings.judge_timeout,
            max_retries=0,
            streaming=False,
            disable_thinking=False,
        )
    )


def _judge_input(request: JudgeRequest) -> str:
    refs = request.source_refs
    if isinstance(refs, dict):
        refs = refs.get("sources") or []
    ref_meta = [
        {
            key: item.get(key)
            for key in ("documentId", "chunkId", "version", "heading", "score")
            if item.get(key) is not None
        }
        for item in (refs or [])[:10]
        if isinstance(item, dict)
    ]
    return json.dumps(
        {
            "userQuestion": request.user_text,
            "assistantAnswer": request.assistant,
            "intent": request.intent,
            "toolsCalled": request.tools_called,
            "sourceRefs": ref_meta,
            "deterministicVerifierPassed": request.verifier_passed,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _parse_result(content: Any) -> dict[str, Any]:
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item) for item in content
        )
    match = _JSON_RE.search(str(content or ""))
    if not match:
        raise ValueError("judge response did not contain JSON")
    payload = json.loads(match.group(0))
    result: dict[str, Any] = {}
    for dimension in _DIMENSIONS:
        value = float(payload[dimension])
        if not 0 <= value <= 1:
            raise ValueError(f"judge score out of range: {dimension}")
        result[dimension] = value
    result["reason"] = str(payload.get("reason") or "")[:120]
    return result


judge_service = JudgeService()
