from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.data_analyst_service import (
    DataAnalysisBranch,
    DataAnalysisPlan,
    DataAnalystService,
    DataNarrative,
    _contract_failure,
    _deterministic_narrative,
    _response_contract_errors,
    _structured_json_llm,
)


def _settings(*, timeout_ms: int = 3000, request_timeout_seconds: float = 45):
    return SimpleNamespace(
        data_analyst_enabled=True,
        analytics_max_days=90,
        analytics_max_rows=200,
        analytics_max_result_bytes=1_000_000,
        analytics_cursor_ttl_seconds=900,
        analytics_query_timeout_ms=timeout_ms,
        analytics_model_timeout_seconds=10,
        analytics_request_timeout_seconds=request_timeout_seconds,
        analytics_eval_fixed_now="2026-08-27T12:00:00",
        internal_token="foundation-test-cursor-secret",
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

    @asynccontextmanager
    async def snapshot():
        yield SimpleNamespace(
            cursor=object(),
            data_as_of="2026-08-27T12:00:00.000000+08:00",
        )

    monkeypatch.setattr("app.services.data_analyst_service.acquire_analytics_snapshot", snapshot)
    monkeypatch.setattr(
        "app.services.data_analyst_service.analytics_result_service.freeze",
        AsyncMock(return_value=None),
    )
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


def test_data_analyst_numeric_narrative_is_derived_from_rows():
    narrative = _deterministic_narrative(
        _plan(),
        [
            {
                "date": "2026-08-06",
                "gross_paid_amount": 3799.0,
                "completed_refund_amount": 488.0,
            },
            {
                "date": "2026-08-07",
                "gross_paid_amount": 6379.0,
                "completed_refund_amount": 0.0,
            },
            {
                "date": "2026-08-09",
                "gross_paid_amount": 14.0,
                "completed_refund_amount": 0.0,
            },
        ],
    )

    assert "已支付金额最大值为6379.00 CNY（2026-08-07）" in narrative.answer
    assert "最小值为14.00 CNY（2026-08-09）" in narrative.answer
    assert "计划范围为7天，返回3个有数据日期" in narrative.answer
    assert "3799元最高" not in narrative.answer


@pytest.mark.parametrize(
    ("question", "clarification_question", "choice_ids"),
    [
        (
            "最近最好卖的商品有哪些？",
            "“最近”和“最好卖”分别按哪个时间范围与指标定义？",
            [
                "LAST_7D_PAID_UNITS",
                "LAST_28D_PAID_UNITS",
                "LAST_7D_GROSS_ITEM_AMOUNT",
            ],
        ),
        (
            "销售最近怎么样？",
            "你希望按哪种时间范围和粒度查看哪些销售运营指标？",
            ["LAST_7D_DAILY_CORE", "LAST_28D_TOTAL_CORE"],
        ),
        (
            "看一下库存有问题的商品。",
            "“库存有问题”具体指当前缺货、当前低库存，还是人工补货建议？",
            ["CURRENT_OUT_OF_STOCK", "CURRENT_LOW_AND_OUT", "REPLENISHMENT_SUGGESTED"],
        ),
        (
            "哪个推荐渠道效果最好？",
            "“效果最好”希望按哪项 VERIFIED 事件日指标比较检索模式？",
            ["LAST_7D_CLICK_RATE", "LAST_7D_PAYMENT_RATE", "LAST_7D_PAYMENT_COUNT"],
        ),
        (
            "哪个 Agent 表现最差？",
            "“表现最差”希望按哪项技术运行指标比较 Agent？",
            ["LAST_7D_FAILURE_COUNT", "LAST_7D_FAILURE_RATE", "LAST_7D_LATENCY"],
        ),
        (
            "汇总一下退款情况。",
            "你希望按哪种退款口径和粒度汇总？",
            [
                "LAST_7D_COMPLETED_REFUND_TOTAL",
                "LAST_7D_REQUEST_AND_COMPLETION",
                "LAST_7D_PRODUCT_REFUNDED_UNITS",
            ],
        ),
        (
            "最近履约情况如何？",
            "“最近履约情况”希望看哪段期间及哪组指标？",
            ["LAST_7D_ORDER_STATUS", "LAST_7D_AFTER_SALES", "LAST_7D_FULL_VIEW"],
        ),
        (
            "报价最好的商品是哪个？",
            "“报价最好”希望按哪项报价快照指标比较商品？",
            ["LOWEST_ESTIMATED_PAYABLE", "MOST_COUPON_AVAILABLE", "MOST_IN_STOCK_QUOTES"],
        ),
        (
            "工具表现怎么样？",
            "“工具表现”希望按哪项技术调用指标查看？",
            ["LAST_7D_COUNTS", "LAST_7D_FAILURE_RATE", "LAST_7D_LATENCY"],
        ),
        (
            "商品 P100 最近表现如何？",
            "“P100 最近表现”希望看哪段期间和哪类表现？",
            [
                "LAST_7D_PRODUCT_SALES",
                "LAST_7D_RECOMMENDATION_EVENTS",
                "LAST_7D_OFFER_QUALITY",
            ],
        ),
    ],
)
@pytest.mark.asyncio
async def test_data_analyst_clarifications_are_catalog_driven_without_model_call(
    monkeypatch,
    question,
    clarification_question,
    choice_ids,
):
    factory = Mock(side_effect=AssertionError("ambiguous metric must not call the model"))
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)
    monkeypatch.setattr("app.services.data_analyst_service.create_memory_llm", factory)

    plan = await DataAnalystService()._plan(question)

    assert plan.status == "NEEDS_CLARIFICATION"
    assert plan.clarification_question == clarification_question
    assert [option.choice_id for option in plan.clarification_options] == choice_ids
    assert all("2026-08-27" in option.answer_suffix for option in plan.clarification_options)
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_clarify_response_has_owner_token_ttl_and_no_query_contract(monkeypatch):
    service = DataAnalystService()
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)
    monkeypatch.setattr(
        "app.services.data_analyst_service.analytics_clarification_service.issue",
        AsyncMock(
            return_value={
                "clarificationToken": "acl_fixed",
                "clarificationTokenTtlSeconds": 900,
                "clarificationTokenExpiresAt": "2026-08-27T12:15:00+08:00",
                "clarificationOptions": [
                    {"choiceId": "A", "label": "A", "answerSuffix": "A"},
                    {"choiceId": "B", "label": "B", "answerSuffix": "B"},
                ],
            }
        ),
    )
    _stub_episode(monkeypatch)

    result = await service.ask("销售最近怎么样？", admin_id="admin")

    assert result["outcome"] == "CLARIFY"
    assert result["completion"] == "NOT_APPLICABLE"
    assert result["queryExecuted"] is False
    assert result["catalogVersion"] == "analytics-provisional-v0.20260827"
    assert result["dataAsOf"] == "2026-08-27T12:00:00+08:00"
    assert result["clarificationTokenTtlSeconds"] == 900


@pytest.mark.parametrize(
    ("question", "reason_code", "required_fact"),
    [
        ("计算今年每月销售额同比和环比。", "UNSUPPORTED_ANALYTIC_OPERATION", "V0 不支持同比、环比或窗口函数"),
        ("按曝光 cohort 计算 7 天支付转化率。", "UNSUPPORTED_COHORT_SEMANTICS", "不能形成曝光 cohort"),
        ("证明推荐系统导致了销量增长。", "CAUSAL_CLAIM_UNSUPPORTED", "不能识别因果"),
        ("给我 2026-08-21 到 2026-08-27 的审计确认收入。", "FINANCIAL_METRIC_UNVERIFIED", "净支付额是暂定运营口径"),
        ("列出 2026-07-01 每个 SKU 的历史库存。", "HISTORICAL_INVENTORY_UNAVAILABLE", "只保留当前快照"),
        ("根据 confidence 告诉我每个 SKU 下周缺货的概率。", "PROBABILITY_UNAVAILABLE", "不是概率"),
        ("把商品销量和当前库存 Join 后计算售罄率。", "JOIN_OUT_OF_V0_SCOPE", "没有已确认售罄率口径"),
        ("预测下个月全站销售收入。", "FORECAST_METRIC_UNAVAILABLE", "现有目录没有销售预测指标"),
        ("计算最近 28 天每个商品的 7 日移动平均销量。", "WINDOW_FUNCTION_OUT_OF_V0_SCOPE", "不支持窗口函数或移动平均"),
        ("今天实际发货了多少单，按发货发生时间统计。", "FULFILLMENT_EVENT_TIME_UNAVAILABLE", "没有发货事件时间"),
    ],
)
@pytest.mark.asyncio
async def test_policy_abstain_has_deterministic_required_facts_and_no_query(
    monkeypatch,
    question,
    reason_code,
    required_fact,
):
    factory = Mock(side_effect=AssertionError("policy abstain must not call the model"))
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)
    monkeypatch.setattr("app.services.data_analyst_service.create_memory_llm", factory)
    _stub_episode(monkeypatch)

    result = await DataAnalystService().ask(question, admin_id="admin")

    assert result["outcome"] == "ABSTAIN"
    assert result["completion"] == "NOT_APPLICABLE"
    assert result["reasonCode"] == reason_code
    assert result["queryExecuted"] is False
    assert result["dataAsOf"] == "2026-08-27T12:00:00+08:00"
    assert required_fact in " ".join(result["requiredFacts"])
    assert "no SQL" in result["requiredFacts"]
    assert "catalog/capability boundary" in result["capabilityBoundary"]
    factory.assert_not_called()


def test_response_contract_fails_closed_on_forbidden_answer_claim():
    result = {
        "runId": "run-contract",
        "outcome": "ANSWER",
        "completion": "COMPLETE",
        "answer": "统计期间：2026-08-21 至 2026-08-27；这是正式转化率。",
        "catalogVersion": "analytics-provisional-v0.20260827",
        "dataAsOf": "2026-08-27T12:00:00+08:00",
        "answerBoundary": {"forbiddenClaims": ["正式转化率"]},
    }

    errors = _response_contract_errors(result)
    failure = _contract_failure(result, errors)

    assert errors == ["FORBIDDEN_CLAIM:正式转化率"]
    assert failure["outcome"] is None
    assert failure["completion"] == "FAILED"
    assert failure["status"] == "ANALYTICS_RESPONSE_CONTRACT_FAILED"


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
    assert result["outcome"] == "ANSWER"
    assert result["completion"] == "COMPLETE"
    assert result["lineage"] == ["analytics_sales_daily"]
    assert result["chart"] == {
        "type": "line",
        "x": "date",
        "series": ["gross_paid_amount", "completed_refund_amount"],
    }
    assert result["answer"].startswith("支付额上升，退款额保持为零。")
    assert "dataAsOf=2026-08-27T12:00:00.000000+08:00" in result["answer"]
    assert "暂定口径，仅供运营核对" in result["answer"]
    assert result["rows"][0] == {
        "date": "2026-08-01",
        "gross_paid_amount": "100.25",
        "completed_refund_amount": "0.00",
    }
    assert result["columnTypes"]["gross_paid_amount"]["type"] == "DECIMAL"
    assert result["warnings"] == ["RESULT_SNAPSHOT_UNAVAILABLE"]
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
async def test_data_analyst_requires_explain_without_expanding_reader_privileges(monkeypatch):
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
    assert result["outcome"] == "ANSWER"
    assert result["completion"] == "COMPLETE"
    assert result["scanEstimate"] is None
    assert result["explain"] == []
    assert result["explainDiagnostic"] == {
        "status": "UNAVAILABLE",
        "reasonCode": "EXPLAIN_UNAVAILABLE_VIEW_PRIVILEGE",
        "databaseErrorCode": 1345,
        "attempted": True,
    }
    assert "EXPLAIN_UNAVAILABLE_VIEW_PRIVILEGE" in result["warnings"]
    execute.assert_awaited_once()
    assert events.count("DATA_ANALYST_EXPLAIN") == 1


def _branch_plan() -> DataAnalysisPlan:
    return DataAnalysisPlan(
        interpretation="销售、推荐质量联合诊断",
        branches=[
            DataAnalysisBranch(
                branch_id="sales",
                purpose="核对销售趋势",
                semantic_view="analytics_sales_daily",
                metrics=["gross_paid_amount", "net_paid_amount"],
                dimensions=["date"],
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 7),
            ),
            DataAnalysisBranch(
                branch_id="quality",
                purpose="核对推荐长期质量",
                semantic_view="analytics_recommendation_quality_daily",
                metrics=["payment_count", "refund_count", "negative_review_count"],
                dimensions=["date"],
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 7),
            ),
        ],
    )


def test_inventory_stockout_is_a_governed_plan_metric(monkeypatch):
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)
    plan = DataAnalysisPlan(
        interpretation="缺货 SKU 趋势",
        branches=[
            DataAnalysisBranch(
                branch_id="stockout",
                purpose="按快照日期统计 stock<=0 的 SKU 数量",
                semantic_view="analytics_inventory_risk",
                metrics=["stockout_sku_count"],
                dimensions=["date"],
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 7),
            )
        ],
    )

    validated = DataAnalystService._validate_plan_dates_and_contract(
        plan,
        default_start=date(2026, 8, 1),
        default_end=date(2026, 8, 7),
    )

    assert validated.metrics == ["stockout_sku_count"]
    assert validated.semantic_view == "analytics_inventory_risk"
    assert validated.dimensions == ["snapshot_date"]


def test_data_analysis_plan_normalizes_null_legacy_lists():
    plan = DataAnalysisPlan.model_validate(
        {
            "status": "READY",
            "metrics": None,
            "dimensions": None,
            "branches": [
                {
                    "branch_id": "sales",
                    "purpose": "支付趋势",
                    "semantic_view": "analytics_sales_daily",
                    "metrics": ["gross_paid_amount"],
                    "dimensions": ["date"],
                }
            ],
        }
    )

    assert plan.metrics == ["gross_paid_amount"]
    assert plan.dimensions == ["date"]


@pytest.mark.asyncio
async def test_data_analyst_plan_retries_one_invalid_structured_result(monkeypatch):
    service = DataAnalystService()
    valid = DataAnalysisPlan(
        interpretation="库存缺货趋势",
        branches=[
            DataAnalysisBranch(
                branch_id="stockout",
                purpose="统计缺货 SKU 数量",
                semantic_view="analytics_inventory_risk",
                metrics=["stockout_sku_count"],
                dimensions=["snapshot_date"],
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 7),
            )
        ],
    )
    responses = [
        {"parsed": None, "parsing_error": ValueError("invalid")},
        {"parsed": valid.model_dump(mode="json"), "parsing_error": None},
    ]
    invoke = AsyncMock(side_effect=responses)
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)
    monkeypatch.setattr(
        "app.services.data_analyst_service._structured_json_llm", lambda _schema: object()
    )
    monkeypatch.setattr("app.services.data_analyst_service.invoke_llm_with_metrics", invoke)

    plan = await service._plan("分析最近7天缺货 SKU 数量趋势")

    assert plan.branches[0].metrics == ["stockout_sku_count"]
    assert invoke.await_count == 2


@pytest.mark.asyncio
async def test_data_analyst_metric_tree_runs_in_stable_snapshot_order(monkeypatch):
    service = DataAnalystService()
    plan = _branch_plan()
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)

    execution_order: list[str] = []

    async def execute(_question, _plan, branch, *, run_id, cursor=None, access_policy=None):
        execution_order.append(branch.branch_id)
        await asyncio.sleep(0 if branch.branch_id == "quality" else 0.01)
        return {
            "branchId": branch.branch_id,
            "purpose": branch.purpose,
            "status": "SUCCEEDED",
            "answer": f"{branch.branch_id} 完成",
            "highlights": [branch.branch_id],
            "sql": f"SELECT date FROM {branch.semantic_view} LIMIT 200",
            "columns": ["date"],
            "rows": [{"date": "2026-08-01"}],
            "lineage": [branch.semantic_view],
            "explain": [],
        }

    monkeypatch.setattr(service, "_execute_metric_branch", execute)
    _stub_episode(monkeypatch)

    result = await service._ask_metric_tree(
        "为什么最近销售和推荐质量变化？",
        plan,
        run_id="run-tree",
        started=time.perf_counter(),
    )

    assert [item["branchId"] for item in result["branches"]] == ["sales", "quality"]
    assert [item["branchId"] for item in result["queries"]] == ["sales", "quality"]
    assert execution_order == ["sales", "quality"]
    assert result["completion"] == "COMPLETE"
    assert result["causalCaution"] == "相关性不等于因果关系；以下结果只用于定位待验证假设。"


@pytest.mark.asyncio
async def test_data_analyst_metric_tree_keeps_partial_success(monkeypatch):
    service = DataAnalystService()
    plan = _branch_plan()
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)

    async def execute(_question, _plan, branch, *, run_id, cursor=None, access_policy=None):
        if branch.branch_id == "quality":
            raise RuntimeError("simulated branch failure")
        return {
            "branchId": branch.branch_id,
            "purpose": branch.purpose,
            "status": "SUCCEEDED",
            "answer": "销售分支完成",
            "highlights": [],
            "sql": _sql(),
            "columns": ["date"],
            "rows": [{"date": "2026-08-01"}],
            "lineage": [branch.semantic_view],
            "explain": [],
        }

    monkeypatch.setattr(service, "_execute_metric_branch", execute)
    _stub_episode(monkeypatch)

    result = await service._ask_metric_tree(
        "为什么最近销售和推荐质量变化？",
        plan,
        run_id="run-partial",
        started=time.perf_counter(),
    )

    assert result["status"] == "SUCCEEDED"
    assert result["outcome"] == "ANSWER"
    assert result["completion"] == "PARTIAL"
    assert "PARTIAL_METRIC_TREE" in result["warnings"]
    assert result["branches"][1]["status"] == "DATA_ANALYST_BRANCH_FAILED"


def test_data_analysis_plan_rejects_duplicate_branch_ids(monkeypatch):
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)
    plan = _branch_plan()
    plan.branches[1].branch_id = "sales"
    with pytest.raises(ValueError, match="DATA_ANALYST_BRANCH_DUPLICATE"):
        DataAnalystService._validate_plan_dates_and_contract(
            plan,
            default_start=date(2026, 8, 1),
            default_end=date(2026, 8, 7),
        )
