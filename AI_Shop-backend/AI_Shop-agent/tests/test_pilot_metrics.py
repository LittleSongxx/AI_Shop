from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.pilot_metrics_service import (
    summarize_performance,
    summarize_pilot_metrics,
)


def _run(
    user_id: str,
    *,
    verified: bool = False,
    confirmed: bool = False,
    completed_at: datetime | None = None,
) -> dict:
    completed = completed_at or datetime(2026, 8, 1, 8, tzinfo=timezone.utc)
    return {
        "run_id": f"run-{user_id}",
        "user_id": user_id,
        "status": "SUCCEEDED",
        "quality_json": {"verifierPassed": verified},
        "reward_signals_json": {"userConfirmedSuccess": confirmed},
        "started_at": completed - timedelta(seconds=1),
        "completed_at": completed,
        "evidence_source": "LOCAL_PILOT",
        "latency_ms": 1000,
        "ttft_ms": 200,
        "step_count": 4,
        "model_calls": 1,
        "tool_calls": 1,
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_cny": 0.02,
    }


def _event(
    event_type: str,
    *,
    occurred_at: datetime,
    user_id: str = "u1",
    request_id: str = "request-1",
    product_id: str = "product-1",
) -> dict:
    return {
        "event_type": event_type,
        "user_id": user_id,
        "request_id": request_id,
        "product_id": product_id,
        "occurred_at": occurred_at,
    }


def test_verified_success_never_uses_http_or_run_success_alone():
    completed = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)
    runs = [
        _run("unverified", completed_at=completed),
        _run("verified", verified=True, completed_at=completed),
        _run("confirmed", confirmed=True, completed_at=completed),
    ]

    result = summarize_pilot_metrics(runs, [])

    assert result["tasks"]["terminal"] == 3
    assert result["tasks"]["verifiedSuccess"] == {
        "numerator": 2,
        "denominator": 3,
        "rate": 0.666667,
    }


def test_fcr_only_excludes_support_contact_inside_24_hour_window():
    completed = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)
    runs = [
        _run("inside", verified=True, completed_at=completed),
        _run("outside", verified=True, completed_at=completed),
    ]
    events = [
        _event(
            "SUPPORT_CONTACT",
            user_id="inside",
            occurred_at=completed + timedelta(hours=2),
        ),
        _event(
            "SUPPORT_CONTACT",
            user_id="outside",
            occurred_at=completed + timedelta(hours=25),
        ),
    ]

    result = summarize_pilot_metrics(runs, events)

    assert result["tasks"]["fcr24h"] == {
        "numerator": 1,
        "denominator": 2,
        "rate": 0.5,
    }


def test_recommendation_windows_are_24_hours_and_7_days():
    impression_at = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)
    events = [
        _event("IMPRESSION", occurred_at=impression_at),
        _event("CLICK", occurred_at=impression_at + timedelta(hours=23)),
        _event("ADD_TO_CART", occurred_at=impression_at + timedelta(hours=25)),
        _event("PAYMENT", occurred_at=impression_at + timedelta(days=6)),
        _event("REFUND", occurred_at=impression_at + timedelta(days=8)),
    ]

    funnel = summarize_pilot_metrics([], events)["funnel"]

    assert funnel["clickWithin24h"]["numerator"] == 1
    assert funnel["addToCartWithin24h"]["numerator"] == 0
    assert funnel["paymentWithin7d"]["numerator"] == 1
    assert funnel["negativeOutcomeWithin7d"]["numerator"] == 0
    assert all(metric["denominator"] == 1 for metric in funnel.values() if isinstance(metric, dict))


def test_performance_discloses_sample_size_and_suppresses_tiny_p99():
    result = summarize_performance([_run("one", verified=True)])

    assert result["latencyMs"]["sampleSize"] == 1
    assert result["latencyMs"]["p50"] == 1000
    assert result["latencyMs"]["p99"] is None
    assert result["latencyMs"]["p99Status"] == "样本少于 100，未报告"
    assert result["costPerVerifiedSuccessCny"] == 0.02


def test_no_real_user_sample_is_reported_as_not_collected():
    result = summarize_pilot_metrics([_run("local", verified=True)], [])

    assert result["realUserStatus"] == "未采集"
    assert result["sampleSources"] == {"LOCAL_PILOT": 1}
