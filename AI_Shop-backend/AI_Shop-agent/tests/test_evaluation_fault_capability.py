from __future__ import annotations

import asyncio
import fnmatch

import pytest

from app.config.settings import get_settings
from app.services.evaluation_fault_service import (
    FaultCapabilityRejected,
    McpFaultCapabilityScope,
    active_mcp_fault_meta,
    consume_api_fault_capability,
    consume_mcp_fault_capability,
    prepare_worker_fault,
    record_fault_events,
    register_fault_capability,
    wait_for_fault_events,
)
from app.services.redis_service import redis_service
from evaluation.core.fault_injection import (
    FailureInjectionScope,
    fault_point,
    parse_fault_scenario,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def set(self, key, value, *, nx=False, ex=None):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def getdel(self, key):
        return self.values.pop(key, None)

    async def exists(self, key):
        return int(key in self.values)

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def expire(self, key, seconds):
        del key, seconds
        return True

    async def lrange(self, key, start, end):
        values = self.lists.get(key, [])
        return list(values[start:] if end == -1 else values[start : end + 1])

    async def delete(self, *keys):
        deleted = 0
        for key in keys:
            deleted += int(self.values.pop(key, None) is not None)
            deleted += int(self.lists.pop(key, None) is not None)
        return deleted

    async def scan_iter(self, match, count=100):
        del count
        for key in [*self.values, *self.lists]:
            if fnmatch.fnmatch(key, match):
                yield key


@pytest.fixture
def enabled_fault_store(monkeypatch):
    fake = _FakeRedis()
    old_client = redis_service._client
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AI_EVAL_ENABLE_FAULT_INJECTION", "true")
    monkeypatch.setenv("AISHOP_INTERNAL_TOKEN", "unit-test-internal-secret")
    get_settings.cache_clear()
    redis_service._client = fake
    try:
        yield fake
    finally:
        redis_service._client = old_client
        get_settings.cache_clear()


def _scenario(target: str, mode: str):
    return parse_fault_scenario(
        {
            "id": f"{target}-{mode}",
            "target": target,
            "mode": mode,
            "gateMode": "HARD",
            "expected": {
                "fallbackAllowed": False,
                "unsafeAnswer": False,
                "hardConstraintBypass": False,
                "terminalState": "FAILED",
            },
        }
    )


@pytest.mark.asyncio
async def test_root_capability_is_one_shot_and_bound(enabled_fault_store):
    registered = await register_fault_capability(
        _scenario("worker-deadline", "timeout"),
        user_id="ev-user",
        request_id="req-1",
        trial_id="trial-1",
    )
    descriptor = await consume_api_fault_capability(
        registered.token,
        user_id="ev-user",
        request_id="req-1",
        trial_id="trial-1",
    )

    assert registered.token not in str(descriptor)
    assert descriptor["binding"] == {
        "userId": "ev-user",
        "requestId": "req-1",
        "trialId": "trial-1",
    }
    with pytest.raises(FaultCapabilityRejected, match="missing or consumed"):
        await consume_api_fault_capability(
            registered.token,
            user_id="ev-user",
            request_id="req-1",
            trial_id="trial-1",
        )


@pytest.mark.asyncio
async def test_binding_mismatch_burns_capability(enabled_fault_store):
    registered = await register_fault_capability(
        _scenario("worker-deadline", "timeout"),
        user_id="ev-user",
        request_id="req-1",
        trial_id="trial-1",
    )
    with pytest.raises(FaultCapabilityRejected, match="binding mismatch"):
        await consume_api_fault_capability(
            registered.token,
            user_id="another-user",
            request_id="req-1",
            trial_id="trial-1",
        )
    with pytest.raises(FaultCapabilityRejected, match="missing or consumed"):
        await consume_api_fault_capability(
            registered.token,
            user_id="ev-user",
            request_id="req-1",
            trial_id="trial-1",
        )


@pytest.mark.asyncio
async def test_worker_descriptor_is_signed_and_injects_only_once(enabled_fault_store):
    registered = await register_fault_capability(
        _scenario("worker-deadline", "timeout"),
        user_id="ev-user",
        request_id="req-1",
        trial_id="trial-1",
    )
    descriptor = await consume_api_fault_capability(
        registered.token,
        user_id="ev-user",
        request_id="req-1",
        trial_id="trial-1",
    )
    payload = {
        "userId": "ev-user",
        "requestId": "req-1",
        "evaluationTrialId": "trial-1",
        "runId": "run-1",
        "evaluationFault": descriptor,
    }
    activation = await prepare_worker_fault(payload)
    assert activation.authorized is not None
    assert activation.authorized.scenario.target == "worker-deadline"
    assert (await prepare_worker_fault(payload)).active is False

    tampered = {**descriptor, "evidenceId": "efe_" + "0" * 32}
    with pytest.raises(FaultCapabilityRejected, match="signature"):
        await prepare_worker_fault({**payload, "evaluationFault": tampered})


@pytest.mark.asyncio
async def test_mcp_child_capability_uses_hidden_meta_and_records_boundary_event(
    enabled_fault_store,
):
    scenario = _scenario("mcp-tool", "5xx")
    registered = await register_fault_capability(
        scenario,
        user_id="ev-user",
        request_id="req-1",
        trial_id="trial-1",
    )
    descriptor = await consume_api_fault_capability(
        registered.token,
        user_id="ev-user",
        request_id="req-1",
        trial_id="trial-1",
    )
    activation = await prepare_worker_fault(
        {
            "userId": "ev-user",
            "requestId": "req-1",
            "evaluationTrialId": "trial-1",
            "runId": "run-1",
            "evaluationFault": descriptor,
        }
    )
    assert activation.mcp_capability
    with McpFaultCapabilityScope(activation.mcp_capability):
        meta = active_mcp_fault_meta()
        assert active_mcp_fault_meta() is None
    assert meta and set(meta) == {"aishopEvaluationFaultCapability"}

    authorized = await consume_mcp_fault_capability(
        activation.mcp_capability,
        arguments={"userId": "ev-user", "requestId": "req-1", "runId": "run-1"},
    )
    scope = FailureInjectionScope(authorized.scenario)
    with pytest.raises(Exception, match="evaluation fault injected"):
        with scope:
            fault_point("mcp-tool")
    await record_fault_events(authorized, scope.events, process="mcp-server")
    events = await wait_for_fault_events(
        registered.evidence_id,
        scenario_id=scenario.scenario_id,
        timeout_seconds=0,
    )
    injected = [row for row in events if row.get("eventType") == "FAULT_INJECTED"]
    assert len(injected) == 1
    assert injected[0]["process"] == "mcp-server"
    assert activation.mcp_capability not in str(events)


@pytest.mark.asyncio
async def test_mcp_child_capability_is_shared_one_shot_across_child_tasks(
    enabled_fault_store,
):
    del enabled_fault_store
    token = "efc_" + "a" * 40

    async def take_meta():
        await asyncio.sleep(0)
        return active_mcp_fault_meta()

    with McpFaultCapabilityScope(token):
        first, second = await asyncio.gather(
            asyncio.create_task(take_meta()),
            asyncio.create_task(take_meta()),
        )
        assert active_mcp_fault_meta() is None

    observed = [value for value in (first, second) if value is not None]
    assert observed == [{"aishopEvaluationFaultCapability": token}]


@pytest.mark.asyncio
async def test_production_rejects_fault_capabilities_even_when_enabled(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AI_EVAL_ENABLE_FAULT_INJECTION", "true")
    get_settings.cache_clear()
    with pytest.raises(FaultCapabilityRejected, match="forbidden in production"):
        await register_fault_capability(
            _scenario("worker-deadline", "timeout"),
            user_id="ev-user",
            request_id="req-1",
            trial_id="trial-1",
        )
