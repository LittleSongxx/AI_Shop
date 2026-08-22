from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

import app.services.request_idempotency_service as module
from app.api.deps import TokenUserInfo
from app.api.routes import agent
from app.services.request_idempotency_service import (
    AgentRequestIdempotencyConflict,
    AgentRequestIdempotencyLedgerError,
    AgentRequestIdempotencyService,
)


class _FakeCursor:
    def __init__(self, rows: dict[tuple[str, str], dict], lock: asyncio.Lock):
        self.rows = rows
        self.lock = lock
        self.rowcount = 0
        self._row = None

    async def execute(self, sql: str, params=()):
        normalized = " ".join(sql.split()).upper()
        self.rowcount = 0
        self._row = None
        if normalized.startswith("INSERT IGNORE"):
            user_id, key, fingerprint, run_id, status = params
            identity = (str(user_id), str(key))
            if identity not in self.rows:
                self.rows[identity] = {
                    "user_id": user_id,
                    "idempotency_key": key,
                    "request_fingerprint": fingerprint,
                    "run_id": run_id,
                    "message_id": None,
                    "status": status,
                    "response_json": None,
                }
                self.rowcount = 1
            return
        if normalized.startswith("SELECT"):
            user_id, key = params[:2]
            self._row = self.rows.get((str(user_id), str(key)))
            return
        if normalized.startswith("UPDATE"):
            if len(params) == 7:
                status, second, response, user_id, key, fingerprint, expected_status = params
            elif len(params) == 6:
                status, response, user_id, key, fingerprint, expected_status = params
                second = None
            elif len(params) == 5:
                status, response, user_id, key, fingerprint = params
                second = None
                expected_status = None
            else:
                raise AssertionError(f"unexpected UPDATE parameter count: {params}")
            row = self.rows.get((str(user_id), str(key)))
            if (
                row
                and row["request_fingerprint"] == fingerprint
                and (expected_status is None or row["status"] == expected_status)
            ):
                row["status"] = status
                row["response_json"] = response
                if second is not None:
                    row["message_id"] = second
                self.rowcount = 1
            return
        raise AssertionError(f"unexpected SQL: {sql}")

    async def fetchone(self):
        return self._row


class _FakeStore:
    def __init__(self):
        self.rows: dict[tuple[str, str], dict] = {}
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            yield _FakeCursor(self.rows, self.lock)

    @asynccontextmanager
    async def acquire(self):
        async with self.lock:
            yield _FakeCursor(self.rows, self.lock)


@pytest.fixture
def fake_store(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(module, "transaction", store.transaction)
    monkeypatch.setattr(module, "acquire", store.acquire)
    return store


def _fingerprint(service: AgentRequestIdempotencyService, text: str = "找耳机"):
    return service.fingerprint(
        message=text,
        from_product=False,
        consult_product_id=None,
        comparison_product_ids=None,
        image_asset_id=None,
    )


@pytest.mark.asyncio
async def test_concurrent_same_key_has_one_owner_and_one_run(fake_store):
    service = AgentRequestIdempotencyService()
    fp = _fingerprint(service)
    first, second = await asyncio.gather(
        service.reserve(user_id="u1", key="same-key", fingerprint=fp),
        service.reserve(user_id="u1", key="same-key", fingerprint=fp),
    )

    assert sorted((first.owner, second.owner)) == [False, True]
    assert first.run_id == second.run_id
    assert len(fake_store.rows) == 1


@pytest.mark.asyncio
async def test_reusing_key_with_different_payload_is_a_conflict(fake_store):
    service = AgentRequestIdempotencyService()
    await service.reserve(
        user_id="u1", key="same-key", fingerprint=_fingerprint(service, "耳机")
    )
    with pytest.raises(AgentRequestIdempotencyConflict):
        await service.reserve(
            user_id="u1", key="same-key", fingerprint=_fingerprint(service, "手机")
        )


@pytest.mark.asyncio
async def test_replay_returns_the_original_response(fake_store):
    service = AgentRequestIdempotencyService()
    reservation = await service.reserve(
        user_id="u1", key="replay-key", fingerprint=_fingerprint(service)
    )
    await service.complete(
        reservation,
        {"status": "success", "code": 200, "data": {"messageId": 7, "runId": reservation.run_id}},
        message_id=7,
    )

    replay = await service.wait(
        await service.reserve(
            user_id="u1", key="replay-key", fingerprint=_fingerprint(service)
        ),
        timeout=0,
    )
    assert replay.resolved
    assert replay.message_id == 7
    assert replay.response["data"]["runId"] == reservation.run_id


@pytest.mark.asyncio
async def test_failed_response_is_replayed_without_reexecuting(fake_store):
    service = AgentRequestIdempotencyService()
    reservation = await service.reserve(
        user_id="u1", key="failed-key", fingerprint=_fingerprint(service)
    )
    await service.fail(
        reservation,
        {"status": "error", "code": 500, "info": "provider unavailable"},
    )

    replay = await service.wait(
        await service.reserve(
            user_id="u1", key="failed-key", fingerprint=_fingerprint(service)
        ),
        timeout=0,
    )

    assert replay.resolved
    assert replay.state == "FAILED"
    assert replay.response == {
        "status": "error",
        "code": 500,
        "info": "provider unavailable",
    }


@pytest.mark.asyncio
async def test_terminal_response_cannot_be_overwritten(fake_store):
    service = AgentRequestIdempotencyService()
    reservation = await service.reserve(
        user_id="u1", key="terminal-key", fingerprint=_fingerprint(service)
    )
    original = {"status": "success", "code": 200, "data": {"messageId": 8}}
    await service.complete(reservation, original, message_id=8)

    with pytest.raises(AgentRequestIdempotencyLedgerError):
        await service.fail(reservation, {"status": "error", "code": 500})

    assert fake_store.rows[("u1", "terminal-key")]["response_json"]


@pytest.mark.asyncio
async def test_complete_rowcount_zero_is_accepted_only_for_identical_replay(fake_store):
    service = AgentRequestIdempotencyService()
    reservation = await service.reserve(
        user_id="u1", key="complete-key", fingerprint=_fingerprint(service)
    )
    response = {"status": "success", "code": 200, "data": {"messageId": 9}}
    await service.complete(reservation, response, message_id=9)
    # A second completion with the same envelope is idempotent; a changed
    # envelope must fail closed instead of replacing the durable result.
    await service.complete(reservation, response, message_id=9)
    with pytest.raises(AgentRequestIdempotencyLedgerError):
        await service.complete(
            reservation,
            {"status": "success", "code": 200, "data": {"messageId": 99}},
            message_id=99,
        )


def test_key_and_fingerprint_boundaries_are_fail_closed():
    service = AgentRequestIdempotencyService()
    with pytest.raises(ValueError, match="1 到 160"):
        service.normalize_key("")
    with pytest.raises(ValueError, match="1 到 160"):
        service.normalize_key("x" * 161)


@pytest.mark.asyncio
async def test_invalid_fingerprint_is_rejected_before_database_access(fake_store):
    service = AgentRequestIdempotencyService()
    with pytest.raises(ValueError, match="SHA-256"):
        await service.reserve(user_id="u1", key="key", fingerprint="not-a-hash")
    assert fake_store.rows == {}


@pytest.mark.asyncio
async def test_route_replay_and_conflict_do_not_reexecute_or_consume_rate_limit(
    fake_store, monkeypatch
):
    orchestrate = AsyncMock(
        return_value={
            "messageId": 41,
            "runId": "idem-route-run",
            "deliveryState": "QUEUED",
        }
    )
    monkeypatch.setattr(agent.agent_orchestrator, "send_message", orchestrate)
    user = TokenUserInfo(user_id="u-route")

    async def call(text: str):
        return await agent.send_message(
            message=text,
            fromProduct="false",
            consultProductId=None,
            comparisonProductIds=None,
            imageAssetId=None,
            x_request_id="req-route",
            x_idempotency_key="route-key",
            x_evaluation_trial_id=None,
            x_evaluation_fault_capability=None,
            user=user,
        )

    first = await call("找耳机")
    replay = await call("找耳机")
    conflict = await call("找手机")

    assert first.model_dump(mode="json") == replay.model_dump(mode="json")
    assert conflict.code == 409
    assert orchestrate.await_count == 1


@pytest.mark.asyncio
async def test_route_concurrent_duplicate_requests_create_one_execution(
    fake_store, monkeypatch
):
    calls = 0

    async def orchestrate(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"messageId": 52, "runId": "idem-concurrent", "deliveryState": "QUEUED"}

    monkeypatch.setattr(agent.agent_orchestrator, "send_message", orchestrate)
    user = TokenUserInfo(user_id="u-concurrent")
    request = {
        "message": "找相机",
        "fromProduct": "false",
        "consultProductId": None,
        "comparisonProductIds": None,
        "imageAssetId": None,
        "x_request_id": "req-concurrent",
        "x_idempotency_key": "concurrent-key",
        "x_evaluation_trial_id": None,
        "x_evaluation_fault_capability": None,
        "user": user,
    }

    responses = await asyncio.gather(
        agent.send_message(**request), agent.send_message(**request)
    )

    assert calls == 1
    assert responses[0].model_dump(mode="json") == responses[1].model_dump(mode="json")
    assert len(fake_store.rows) == 1
