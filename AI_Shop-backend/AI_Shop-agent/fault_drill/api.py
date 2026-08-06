from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.constants import AGENT_QUEUE_FAST
from app.db.migrations import run_migrations
from app.db.pool import close_pool, init_pool
from app.services.agent_queue_service import agent_queue_service
from app.services.redis_service import redis_service
from app.services.task_service import agent_task_service
from fault_drill.common import ensure_drill_schema, load_drill_state


class DrillTaskRequest(BaseModel):
    message_id: int = Field(alias="messageId", gt=0)
    user_id: str = Field(alias="userId", min_length=1, max_length=15)
    mode: Literal["normal", "takeover", "checkpoint_failure"] = "normal"
    action_key: str = Field(alias="actionKey", min_length=1, max_length=128)
    duplicate_deliveries: int = Field(1, alias="duplicateDeliveries", ge=1, le=3)
    first_attempt_sleep_seconds: int = Field(
        90, alias="firstAttemptSleepSeconds", ge=1, le=300
    )

    model_config = {"populate_by_name": True}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await asyncio.to_thread(run_migrations)
    await init_pool()
    await redis_service.connect()
    await ensure_drill_schema()
    yield
    await agent_queue_service.close()
    await close_pool()
    await redis_service.close()


app = FastAPI(title="AI Shop isolated fault drill", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "UP", "scope": "isolated-fault-drill"}


@app.post("/tasks")
async def create_task(request: DrillTaskRequest) -> dict:
    payload = {
        "messageId": request.message_id,
        "userId": request.user_id,
        "userMessage": "fault drill deterministic payload",
        "queueName": AGENT_QUEUE_FAST,
        "faultMode": request.mode,
        "actionKey": request.action_key,
        "firstAttemptSleepSeconds": request.first_attempt_sleep_seconds,
        # A non-default source makes the production Worker skip LLM intent refinement.
        "intentDecision": {
            "intent": "CHAT",
            "confidence": 1.0,
            "sentiment": "NEUTRAL",
            "urgency": "NORMAL",
            "risk_level": "LOW",
            "next_action": "ANSWER",
            "source": "fault_drill",
        },
    }
    created = await agent_task_service.create(
        request.message_id,
        request.user_id,
        AGENT_QUEUE_FAST,
        60,
        payload,
    )
    if not created:
        raise HTTPException(status_code=409, detail="messageId already exists")

    delivery_state = "PENDING_RECOVERY"
    publish_error: str | None = None
    try:
        if not await agent_task_service.mark_dispatching(request.message_id):
            raise RuntimeError("task dispatch CAS did not match")
        for _ in range(request.duplicate_deliveries):
            await asyncio.wait_for(
                agent_queue_service.publish(AGENT_QUEUE_FAST, payload),
                timeout=2.0,
            )
        await agent_task_service.mark_queued(request.message_id)
        delivery_state = "QUEUED"
    except Exception as exc:
        publish_error = f"{type(exc).__name__}: {exc}"[:500]
        # Match AgentOrchestrator semantics: DISPATCHING is the durable truth and
        # recovery owns the retry; the HTTP request itself still succeeds.
        await agent_queue_service.close()

    return {
        "messageId": request.message_id,
        "deliveryState": delivery_state,
        "publishError": publish_error,
        "state": await load_drill_state(request.message_id),
    }


@app.get("/tasks/{message_id}")
async def task_state(message_id: int) -> dict:
    state = await load_drill_state(message_id)
    if state["task"] is None:
        raise HTTPException(status_code=404, detail="task not found")
    return state
