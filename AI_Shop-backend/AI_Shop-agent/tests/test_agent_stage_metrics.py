import pytest

from app.harness.metrics.runtime_sensors import (
    AGENT_STAGE_LATENCY,
    AGENT_STAGE_NAMES,
    LLM_TOKEN_TOTAL,
    ORDER_REFERENCE_LATENCY,
    ORDER_REFERENCE_TOTAL,
    ORDER_SELECTION_TOTAL,
    observe_agent_stage,
)


def test_stage_latency_contract_has_only_fixed_low_cardinality_label():
    assert AGENT_STAGE_NAMES == {
        "queue_wait",
        "intent",
        "rag",
        "first_token",
        "tool",
        "generation",
        "total",
    }
    assert tuple(AGENT_STAGE_LATENCY._labelnames) == ("stage",)
    assert not {"query", "userId", "messageId"}.intersection(
        AGENT_STAGE_LATENCY._labelnames
    )


def test_unknown_stage_is_rejected_before_it_can_create_a_series():
    with pytest.raises(ValueError, match="unsupported agent latency stage"):
        observe_agent_stage("query:用户自由文本", 0.1)


def test_llm_token_metric_requires_model_and_fallback_dimensions():
    assert tuple(LLM_TOKEN_TOTAL._labelnames) == ("kind", "model", "fallback")


def test_order_resolution_metrics_keep_business_identifiers_out_of_labels():
    assert tuple(ORDER_REFERENCE_TOTAL._labelnames) == ("intent", "outcome")
    assert tuple(ORDER_SELECTION_TOTAL._labelnames) == ("intent", "outcome")
    assert tuple(ORDER_REFERENCE_LATENCY._labelnames) == ()
    forbidden = {"userId", "user_id", "orderId", "messageId", "productName", "selectionId"}
    assert forbidden.isdisjoint(ORDER_REFERENCE_TOTAL._labelnames)
    assert forbidden.isdisjoint(ORDER_SELECTION_TOTAL._labelnames)
