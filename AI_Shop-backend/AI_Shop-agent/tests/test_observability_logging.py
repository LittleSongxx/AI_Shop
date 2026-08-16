from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from app.observability.logging import add_otel_context


def test_log_processor_only_adds_active_otel_context():
    assert add_otel_context(None, "info", {"event": "outside"}) == {
        "event": "outside"
    }

    context = SpanContext(
        trace_id=int("a" * 32, 16),
        span_id=int("b" * 16, 16),
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=trace.DEFAULT_TRACE_STATE,
    )
    with trace.use_span(NonRecordingSpan(context)):
        enriched = add_otel_context(None, "info", {"event": "inside"})

    assert enriched["trace_id"] == "a" * 32
    assert enriched["span_id"] == "b" * 16


def test_log_processor_preserves_explicit_trace_fields():
    enriched = add_otel_context(
        None,
        "info",
        {"trace_id": "business-trace", "span_id": "business-span"},
    )

    assert enriched["trace_id"] == "business-trace"
    assert enriched["span_id"] == "business-span"
