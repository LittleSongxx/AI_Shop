from datetime import datetime, timedelta
from types import SimpleNamespace

from app.constants import AGENT_QUEUE_FAST, AGENT_QUEUE_HIGH, AGENT_QUEUE_LOW
from app.resilience.circuit_breaker import CircuitBreaker, CircuitState
from app.services.agent_queue_service import AgentQueueService
from app.worker import AgentWorker


def test_queue_policy_keeps_high_risk_above_shopping_traffic():
    high = SimpleNamespace(
        should_handoff=True,
        intent=SimpleNamespace(value="CHAT"),
    )
    low = SimpleNamespace(
        should_handoff=False,
        intent=SimpleNamespace(value="PRODUCT_SEARCH"),
    )

    assert AgentQueueService.queue_for_decision(high) == (AGENT_QUEUE_HIGH, 100)
    assert AgentQueueService.queue_for_decision(low) == (AGENT_QUEUE_LOW, 20)
    assert AgentQueueService.queue_for_decision(
        SimpleNamespace(should_handoff=False, intent=SimpleNamespace(value="INVOICE"))
    ) == (AGENT_QUEUE_FAST, 60)


def test_deadline_check_accepts_datetime_and_iso_payloads():
    now = datetime.now()
    assert AgentWorker._deadline_expired({"deadlineAt": now - timedelta(seconds=1)})
    assert not AgentWorker._deadline_expired({"deadlineAt": (now + timedelta(seconds=30)).isoformat()})
    assert not AgentWorker._deadline_expired({})


def test_circuit_breaker_opens_after_failures_and_closes_on_success():
    breaker = CircuitBreaker("test-policy", failure_threshold=2, recovery_timeout=60)

    breaker.record_failure()
    assert breaker.allow_request()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert not breaker.allow_request()

    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.allow_request()
