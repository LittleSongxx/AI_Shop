from datetime import datetime, timedelta

from app.services.support_service import build_sla_stats


def test_build_sla_stats_calculates_wait_response_resolution_and_overdue():
    now = datetime(2026, 7, 24, 12, 0, 0)
    rows = [
        {
            "status": "RESOLVED",
            "created_at": now - timedelta(minutes=20),
            "assigned_at": now - timedelta(minutes=18),
            "first_response_at": now - timedelta(minutes=17),
            "resolved_at": now - timedelta(minutes=2),
            "assigned_admin": "a1",
        },
        {
            "status": "QUEUED",
            "created_at": now - timedelta(minutes=20),
            "assigned_at": None,
            "first_response_at": None,
            "resolved_at": None,
            "assigned_admin": None,
        },
        {
            "status": "ACTIVE",
            "created_at": now - timedelta(minutes=8),
            "assigned_at": now - timedelta(minutes=1),
            "first_response_at": None,
            "resolved_at": None,
            "assigned_admin": "a2",
        },
    ]

    result = build_sla_stats(
        rows,
        window_hours=24,
        first_response_target=300,
        queue_alert_target=600,
        now=now,
    )

    assert result["totalSessions"] == 3
    assert result["activeSessions"] == 2
    assert result["averageQueueWaitSeconds"] == 270.0
    assert result["averageFirstResponseSeconds"] == 180.0
    assert result["firstResponseSlaRate"] == 1.0
    assert result["overdueQueued"] == 1
    assert result["overdueFirstResponse"] == 1
    assert result["activeByAdmin"] == {"a2": 1}
