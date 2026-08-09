from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.data_analyst_service import (
    DataAnalysisPlan,
    DataAnalystService,
    DataNarrative,
    _structured_json_llm,
)


def _settings(*, timeout_ms: int = 3000, request_timeout_seconds: float = 45):
    return SimpleNamespace(
        data_analyst_enabled=True,
        analytics_max_days=90,
        analytics_max_rows=200,
        analytics_query_timeout_ms=timeout_ms,
        analytics_model_timeout_seconds=10,
        analytics_request_timeout_seconds=request_timeout_seconds,
    )


def _plan() -> DataAnalysisPlan:
    return DataAnalysisPlan(
        semantic_view="analytics_sales_daily",
        metrics=["gross_paid_amount", "completed_refund_amount"],
        dimensions=["date"],
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 7),
        interpretation="最近七天支付与退款趋势",
    )


def _sql() -> str:
    return (
        "SELECT date, gross_paid_amount, completed_refund_amount "
        "FROM analytics_sales_daily WHERE date BETWEEN '2026-08-01' "
        "AND '2026-08-07' ORDER BY date LIMIT 200"
    )


def _stub_episode(monkeypatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(
        "app.services.data_analyst_service.episode_service.start_run",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        "app.services.data_analyst_service.episode_service.record_step",
        lambda event, **_kwargs: events.append(event),
    )
    monkeypatch.setattr(
        "app.services.data_analyst_service.episode_service.finish_run",
        lambda *_args, **_kwargs: None,
    )
    return events


def test_data_analyst_uses_non_thinking_json_mode(monkeypatch):
    llm = Mock()
    structured = object()
    llm.with_structured_output.return_value = structured
    factory = Mock(return_value=llm)
    monkeypatch.setattr("app.services.data_analyst_service.create_memory_llm", factory)

    assert _structured_json_llm(DataAnalysisPlan) is structured
    factory.assert_called_once_with(disable_thinking=True)
    llm.with_structured_output.assert_called_once_with(
        DataAnalysisPlan,
        method="json_mode",
        include_raw=True,
    )


@pytest.mark.asyncio
async def test_data_analyst_clarifies_natural_best_selling_wording_without_model_call(
    monkeypatch,
):
    factory = Mock(side_effect=AssertionError("ambiguous metric must not call the model"))
    monkeypatch.setattr("app.services.data_analyst_service.create_memory_llm", factory)

    plan = await DataAnalystService()._plan("近期什么产品卖的最好")

    assert plan.status == "NEEDS_CLARIFICATION"
    assert "销售金额" in str(plan.clarification_question)
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_data_analyst_success_is_grounded_and_traceable(monkeypatch):
    service = DataAnalystService()
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)
    monkeypatch.setattr(service, "_plan", AsyncMock(return_value=_plan()))
    monkeypatch.setattr(service, "_draft_sql", AsyncMock(return_value=_sql()))
    monkeypatch.setattr(
        service,
        "_narrative",
        AsyncMock(
            return_value=DataNarrative(
                answer="支付额上升，退款额保持为零。", highlights=["支付额上升"]
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.data_analyst_service._explain_sql",
        AsyncMock(return_value=[{"table": "analytics_sales_daily", "type": "range"}]),
    )
    monkeypatch.setattr(
        "app.services.data_analyst_service._execute_sql",
        AsyncMock(
            return_value=[
                {
                    "date": date(2026, 8, 1),
                    "gross_paid_amount": Decimal("100.25"),
                    "completed_refund_amount": Decimal("0.00"),
                },
                {
                    "date": date(2026, 8, 2),
                    "gross_paid_amount": Decimal("160.50"),
                    "completed_refund_amount": Decimal("0.00"),
                },
            ]
        ),
    )
    events = _stub_episode(monkeypatch)

    result = await service.ask("最近七天销售额和退款额趋势如何？", admin_id="admin")

    assert result["status"] == "SUCCEEDED"
    assert result["lineage"] == ["analytics_sales_daily"]
    assert result["chart"] == {
        "type": "line",
        "x": "date",
        "series": ["gross_paid_amount", "completed_refund_amount"],
    }
    assert result["answer"] == "支付额上升，退款额保持为零。"
    assert result["rows"][0] == {
        "date": "2026-08-01",
        "gross_paid_amount": 100.25,
        "completed_refund_amount": 0.0,
    }
    assert events == [
        "DATA_ANALYST_PLAN",
        "DATA_ANALYST_SQL_GUARD",
        "DATA_ANALYST_EXPLAIN",
        "DATA_ANALYST_QUERY",
        "DATA_ANALYST_RESULT",
    ]


@pytest.mark.asyncio
async def test_data_analyst_repairs_sql_at_most_once(monkeypatch):
    service = DataAnalystService()
    draft = AsyncMock(side_effect=["SELECT * FROM analytics_sales_daily LIMIT 200", _sql()])
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)
    monkeypatch.setattr(service, "_plan", AsyncMock(return_value=_plan()))
    monkeypatch.setattr(service, "_draft_sql", draft)
    monkeypatch.setattr(service, "_narrative", AsyncMock(return_value=DataNarrative(answer="完成")))
    monkeypatch.setattr(
        "app.services.data_analyst_service._explain_sql", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "app.services.data_analyst_service._execute_sql", AsyncMock(return_value=[])
    )
    _stub_episode(monkeypatch)
    finish = Mock()
    monkeypatch.setattr("app.services.data_analyst_service.episode_service.finish_run", finish)

    result = await service.ask("最近七天销售额", admin_id="admin")

    assert result["status"] == "EMPTY_RESULT"
    assert draft.await_count == 2
    assert draft.await_args_list[1].kwargs["feedback"] == "SQL_STAR_FORBIDDEN"
    assert finish.call_args.kwargs["status"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_data_analyst_rejects_second_invalid_sql(monkeypatch):
    service = DataAnalystService()
    draft = AsyncMock(return_value="SELECT * FROM analytics_sales_daily LIMIT 200")
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)
    monkeypatch.setattr(service, "_plan", AsyncMock(return_value=_plan()))
    monkeypatch.setattr(service, "_draft_sql", draft)
    explain = AsyncMock()
    monkeypatch.setattr("app.services.data_analyst_service._explain_sql", explain)
    _stub_episode(monkeypatch)

    result = await service.ask("最近七天销售额", admin_id="admin")

    assert result["status"] == "SQL_STAR_FORBIDDEN"
    assert draft.await_count == 2
    explain.assert_not_awaited()


@pytest.mark.asyncio
async def test_data_analyst_normalizes_model_and_pool_failures(monkeypatch):
    service = DataAnalystService()
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)
    monkeypatch.setattr(service, "_plan", AsyncMock(side_effect=RuntimeError("provider secret")))
    _stub_episode(monkeypatch)
    model_result = await service.ask("最近七天销售额", admin_id="admin")
    assert model_result["status"] == "DATA_ANALYST_MODEL_UNAVAILABLE"
    assert "provider secret" not in str(model_result)

    monkeypatch.setattr(service, "_plan", AsyncMock(return_value=_plan()))
    monkeypatch.setattr(service, "_draft_sql", AsyncMock(return_value=_sql()))
    monkeypatch.setattr(
        "app.services.data_analyst_service._explain_sql",
        AsyncMock(side_effect=RuntimeError("analytics DB pool not initialized")),
    )
    pool_result = await service.ask("最近七天销售额", admin_id="admin")
    assert pool_result["status"] == "ANALYTICS_POOL_UNAVAILABLE"


@pytest.mark.asyncio
async def test_data_analyst_query_timeout_is_normalized(monkeypatch):
    async def slow_query(*_args, **_kwargs):
        await asyncio.sleep(0.1)
        return []

    service = DataAnalystService()
    monkeypatch.setattr(
        "app.services.data_analyst_service.get_settings",
        lambda: _settings(timeout_ms=10),
    )
    monkeypatch.setattr(service, "_plan", AsyncMock(return_value=_plan()))
    monkeypatch.setattr(service, "_draft_sql", AsyncMock(return_value=_sql()))
    monkeypatch.setattr(
        "app.services.data_analyst_service._explain_sql", AsyncMock(return_value=[])
    )
    monkeypatch.setattr("app.services.data_analyst_service._execute_sql", slow_query)
    events = _stub_episode(monkeypatch)

    result = await service.ask("最近七天销售额", admin_id="admin")

    assert result["status"] == "QUERY_TIMEOUT"
    assert "DATA_ANALYST_QUERY" in events


@pytest.mark.asyncio
async def test_data_analyst_request_budget_cancels_workflow_and_finishes_run(monkeypatch):
    async def slow_plan(*_args, **_kwargs):
        await asyncio.sleep(0.1)
        return _plan()

    service = DataAnalystService()
    monkeypatch.setattr(
        "app.services.data_analyst_service.get_settings",
        lambda: _settings(request_timeout_seconds=0.01),
    )
    monkeypatch.setattr(service, "_plan", slow_plan)
    events = _stub_episode(monkeypatch)
    finish = Mock()
    monkeypatch.setattr("app.services.data_analyst_service.episode_service.finish_run", finish)

    result = await service.ask("最近七天销售额", admin_id="admin")

    assert result["status"] == "DATA_ANALYST_REQUEST_TIMEOUT"
    assert events == ["DATA_ANALYST_TIMEOUT"]
    assert finish.call_args.args == ("request_timeout",)
    assert finish.call_args.kwargs["status"] == "FAILED"


@pytest.mark.asyncio
async def test_data_analyst_does_not_expand_reader_privileges_for_explain(monkeypatch):
    class ExplainPrivilegeError(Exception):
        pass

    service = DataAnalystService()
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)
    monkeypatch.setattr(service, "_plan", AsyncMock(return_value=_plan()))
    monkeypatch.setattr(service, "_draft_sql", AsyncMock(return_value=_sql()))
    monkeypatch.setattr(
        service,
        "_narrative",
        AsyncMock(return_value=DataNarrative(answer="只读查询完成")),
    )
    monkeypatch.setattr(
        "app.services.data_analyst_service._explain_sql",
        AsyncMock(side_effect=ExplainPrivilegeError(1345, "underlying table denied")),
    )
    execute = AsyncMock(return_value=[{"date": "2026-08-01", "gross_paid_amount": 100}])
    monkeypatch.setattr("app.services.data_analyst_service._execute_sql", execute)
    events = _stub_episode(monkeypatch)

    result = await service.ask("最近七天销售额", admin_id="admin")

    assert result["status"] == "SUCCEEDED"
    assert result["warnings"] == ["EXPLAIN_SKIPPED_VIEW_PRIVILEGE"]
    assert result["explain"] == [{"status": "SKIPPED", "reason": "VIEW_DEFINER_PRIVILEGE_BOUNDARY"}]
    execute.assert_awaited_once()
    assert events.count("DATA_ANALYST_EXPLAIN") == 1
