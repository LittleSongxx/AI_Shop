from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.analytics_policy import evaluate_question_policy
from app.services.analytics_semantic_compiler import (
    SemanticFilter,
    SemanticOrder,
    SemanticPlanUnsupported,
)
from app.services.data_analyst_service import (
    DataAnalysisBranch,
    DataAnalysisPlan,
    DataAnalystService,
    DataNarrative,
    _compile_branch_sql,
    _contract_failure,
    _deterministic_narrative,
    _normalize_supply_chain_plan,
    _quality_disclosures,
    _response_contract_errors,
    _structured_json_llm,
    _supply_chain_disclosures,
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


@pytest.mark.parametrize(
    ("branch", "expected_sql"),
    [
        (
            DataAnalysisBranch(
                branch_id="stockout-details",
                semantic_view="analytics_inventory_risk",
                dimensions=[
                    "snapshot_date",
                    "product_id",
                    "product_name",
                    "property_value_id_hash",
                    "risk_level",
                ],
                metrics=["stock"],
                start_date=date(2026, 8, 27),
                end_date=date(2026, 8, 27),
                filters=[SemanticFilter(column="stock", operator="LTE", value=0)],
            ),
            "SELECT snapshot_date, product_id, product_name, property_value_id_hash, "
            "stock, risk_level FROM analytics_inventory_risk WHERE snapshot_date BETWEEN "
            "'2026-08-27' AND '2026-08-27' AND stock <= 0 ORDER BY product_id ASC, "
            "property_value_id_hash ASC LIMIT 200",
        ),
        (
            DataAnalysisBranch(
                branch_id="stockout-count",
                semantic_view="analytics_inventory_risk",
                dimensions=[],
                metrics=["stockout_sku_count"],
                start_date=date(2026, 8, 27),
                end_date=date(2026, 8, 27),
            ),
            "SELECT SUM(CASE WHEN stock <= 0 THEN 1 ELSE 0 END) AS stockout_sku_count "
            "FROM analytics_inventory_risk WHERE snapshot_date BETWEEN '2026-08-27' "
            "AND '2026-08-27' LIMIT 200",
        ),
        (
            DataAnalysisBranch(
                branch_id="risk-level-count",
                semantic_view="analytics_inventory_risk",
                dimensions=["risk_level"],
                metrics=["stockout_sku_count"],
                start_date=date(2026, 8, 27),
                end_date=date(2026, 8, 27),
            ),
            "SELECT risk_level, COUNT(*) AS sku_count FROM analytics_inventory_risk "
            "WHERE snapshot_date BETWEEN '2026-08-27' AND '2026-08-27' "
            "GROUP BY risk_level ORDER BY risk_level ASC LIMIT 200",
        ),
        (
            DataAnalysisBranch(
                branch_id="low-stock",
                semantic_view="analytics_inventory_risk",
                dimensions=[
                    "product_id",
                    "product_name",
                    "property_value_id_hash",
                    "risk_level",
                ],
                metrics=["stock"],
                start_date=date(2026, 8, 27),
                end_date=date(2026, 8, 27),
                filters=[
                    SemanticFilter(column="stock", operator="BETWEEN", value=1, second_value=10)
                ],
                order_by=[SemanticOrder(column="stock")],
            ),
            "SELECT product_id, product_name, property_value_id_hash, stock, risk_level "
            "FROM analytics_inventory_risk WHERE snapshot_date BETWEEN '2026-08-27' "
            "AND '2026-08-27' AND stock BETWEEN 1 AND 10 ORDER BY stock ASC, product_id "
            "ASC, property_value_id_hash ASC LIMIT 200",
        ),
        (
            DataAnalysisBranch(
                branch_id="stock-top",
                semantic_view="analytics_inventory_risk",
                dimensions=["product_id", "property_value_id_hash"],
                metrics=["stock"],
                start_date=date(2026, 8, 27),
                end_date=date(2026, 8, 27),
                order_by=[SemanticOrder(column="stock", direction="DESC")],
                top_k=3,
            ),
            "SELECT product_id, property_value_id_hash, stock FROM analytics_inventory_risk "
            "WHERE snapshot_date BETWEEN '2026-08-27' AND '2026-08-27' ORDER BY stock DESC, "
            "product_id ASC, property_value_id_hash ASC LIMIT 3",
        ),
        (
            DataAnalysisBranch(
                branch_id="forecast-inputs",
                semantic_view="analytics_inventory_forecast",
                dimensions=["snapshot_date", "product_id", "product_name", "sku_key"],
                metrics=[
                    "current_stock",
                    "inbound_quantity",
                    "ewma_daily_demand",
                    "lead_time_days",
                    "safety_stock",
                    "min_order_quantity",
                    "review_period_days",
                    "suggested_replenish_quantity",
                ],
                start_date=date(2026, 8, 27),
                end_date=date(2026, 8, 27),
            ),
            "SELECT snapshot_date, product_id, product_name, sku_key, current_stock, "
            "inbound_quantity, ewma_daily_demand, lead_time_days, safety_stock, "
            "min_order_quantity, review_period_days, suggested_replenish_quantity FROM "
            "analytics_inventory_forecast WHERE snapshot_date BETWEEN '2026-08-27' AND "
            "'2026-08-27' ORDER BY product_id ASC, sku_key ASC LIMIT 200",
        ),
        (
            DataAnalysisBranch(
                branch_id="replenishment-top",
                semantic_view="analytics_inventory_forecast",
                dimensions=["product_id", "product_name", "sku_key"],
                metrics=["suggested_replenish_quantity"],
                start_date=date(2026, 8, 27),
                end_date=date(2026, 8, 27),
                order_by=[SemanticOrder(column="suggested_replenish_quantity", direction="DESC")],
                top_k=3,
            ),
            "SELECT product_id, product_name, sku_key, suggested_replenish_quantity "
            "FROM analytics_inventory_forecast WHERE snapshot_date BETWEEN '2026-08-27' "
            "AND '2026-08-27' ORDER BY suggested_replenish_quantity DESC, product_id ASC, "
            "sku_key ASC LIMIT 3",
        ),
        (
            DataAnalysisBranch(
                branch_id="low-coverage",
                semantic_view="analytics_inventory_forecast",
                dimensions=["product_id", "product_name", "sku_key"],
                metrics=["coverage_days"],
                start_date=date(2026, 8, 27),
                end_date=date(2026, 8, 27),
                filters=[SemanticFilter(column="coverage_days", operator="LT", value=7)],
                order_by=[SemanticOrder(column="coverage_days")],
            ),
            "SELECT product_id, product_name, sku_key, coverage_days FROM "
            "analytics_inventory_forecast WHERE snapshot_date BETWEEN '2026-08-27' "
            "AND '2026-08-27' AND coverage_days < 7 ORDER BY coverage_days ASC, "
            "product_id ASC, sku_key ASC LIMIT 200",
        ),
        (
            DataAnalysisBranch(
                branch_id="unknown-coverage",
                semantic_view="analytics_inventory_forecast",
                dimensions=["product_id", "product_name", "sku_key"],
                metrics=["coverage_days", "ewma_daily_demand", "confidence"],
                start_date=date(2026, 8, 27),
                end_date=date(2026, 8, 27),
                filters=[SemanticFilter(column="coverage_days", operator="IS_NULL")],
            ),
            "SELECT product_id, product_name, sku_key, coverage_days, ewma_daily_demand, "
            "confidence FROM analytics_inventory_forecast WHERE snapshot_date BETWEEN "
            "'2026-08-27' AND '2026-08-27' AND coverage_days IS NULL ORDER BY "
            "product_id ASC, sku_key ASC LIMIT 200",
        ),
        (
            DataAnalysisBranch(
                branch_id="forecast-comparison",
                semantic_view="analytics_inventory_forecast",
                dimensions=["product_id", "sku_key"],
                metrics=["current_stock", "suggested_replenish_quantity", "confidence"],
                start_date=date(2026, 8, 27),
                end_date=date(2026, 8, 27),
            ),
            "SELECT product_id, sku_key, current_stock, suggested_replenish_quantity, "
            "confidence FROM analytics_inventory_forecast WHERE snapshot_date BETWEEN "
            "'2026-08-27' AND '2026-08-27' ORDER BY product_id ASC, sku_key ASC LIMIT 200",
        ),
        (
            DataAnalysisBranch(
                branch_id="risk-comparison",
                semantic_view="analytics_inventory_risk",
                dimensions=["product_id", "property_value_id_hash", "risk_level"],
                metrics=["stock"],
                start_date=date(2026, 8, 27),
                end_date=date(2026, 8, 27),
            ),
            "SELECT product_id, property_value_id_hash, stock, risk_level FROM "
            "analytics_inventory_risk WHERE snapshot_date BETWEEN '2026-08-27' AND "
            "'2026-08-27' ORDER BY product_id ASC, property_value_id_hash ASC LIMIT 200",
        ),
    ],
)
def test_supply_chain_semantic_compiler_is_deterministic(branch, expected_sql):
    assert _compile_branch_sql(branch) == expected_sql


def test_supply_chain_semantic_compiler_rejects_mysql_backslash_escape():
    branch = DataAnalysisBranch(
        branch_id="unsafe-text-filter",
        semantic_view="analytics_inventory_risk",
        dimensions=["product_id", "property_value_id_hash"],
        metrics=["stock"],
        start_date=date(2026, 8, 27),
        end_date=date(2026, 8, 27),
        filters=[
            SemanticFilter(
                column="risk_level",
                operator="EQ",
                value=r"x\' AND stock > 0",
            )
        ],
    )

    with pytest.raises(SemanticPlanUnsupported, match="SEMANTIC_PLAN_UNSUPPORTED"):
        _compile_branch_sql(branch)


@pytest.mark.parametrize(
    ("branch", "expected_sql"),
    [
        (
            DataAnalysisBranch(
                branch_id="recommendation-detail",
                semantic_view="analytics_recommendation_quality_daily",
                dimensions=["date", "product_id"],
                metrics=[
                    "payment_count",
                    "refund_count",
                    "return_count",
                    "negative_review_count",
                    "support_contact_count",
                    "repeat_purchase_count",
                ],
                start_date=date(2026, 8, 21),
                end_date=date(2026, 8, 27),
                order_by=[SemanticOrder(column="date"), SemanticOrder(column="product_id")],
            ),
            "SELECT date, product_id, payment_count, refund_count, return_count, "
            "negative_review_count, support_contact_count, repeat_purchase_count FROM "
            "analytics_recommendation_quality_daily WHERE date BETWEEN '2026-08-21' AND "
            "'2026-08-27' ORDER BY date ASC, product_id ASC LIMIT 200",
        ),
        (
            DataAnalysisBranch(
                branch_id="recommendation-aggregate",
                semantic_view="analytics_recommendation_quality_daily",
                dimensions=["product_id"],
                metrics=["refund_count", "return_count"],
                start_date=date(2026, 8, 21),
                end_date=date(2026, 8, 27),
                order_by=[SemanticOrder(column="product_id")],
            ),
            "SELECT product_id, SUM(refund_count) AS refund_count, "
            "SUM(return_count) AS return_count FROM analytics_recommendation_quality_daily "
            "WHERE date BETWEEN '2026-08-21' AND '2026-08-27' GROUP BY product_id "
            "ORDER BY product_id ASC LIMIT 200",
        ),
        (
            DataAnalysisBranch(
                branch_id="recommendation-or",
                semantic_view="analytics_recommendation_quality_daily",
                dimensions=["product_id"],
                metrics=["negative_review_count", "support_contact_count"],
                start_date=date(2026, 8, 21),
                end_date=date(2026, 8, 27),
                filters=[
                    SemanticFilter(column="negative_review_count", operator="GT", value=0),
                    SemanticFilter(column="support_contact_count", operator="GT", value=0),
                ],
                order_by=[SemanticOrder(column="product_id")],
            ),
            "SELECT product_id, SUM(negative_review_count) AS negative_review_count, "
            "SUM(support_contact_count) AS support_contact_count FROM "
            "analytics_recommendation_quality_daily WHERE date BETWEEN '2026-08-21' AND "
            "'2026-08-27' GROUP BY product_id HAVING SUM(negative_review_count) + "
            "SUM(support_contact_count) > 0 ORDER BY product_id ASC LIMIT 200",
        ),
        (
            DataAnalysisBranch(
                branch_id="tool-detail",
                semantic_view="analytics_tool_quality_daily",
                dimensions=["date", "agent_id", "tool_name"],
                metrics=["call_count", "success_count", "failure_count", "avg_latency_ms"],
                start_date=date(2026, 8, 21),
                end_date=date(2026, 8, 27),
                order_by=[
                    SemanticOrder(column="date"),
                    SemanticOrder(column="agent_id"),
                    SemanticOrder(column="tool_name"),
                ],
            ),
            "SELECT date, agent_id, tool_name, call_count, success_count, failure_count, "
            "avg_latency_ms FROM analytics_tool_quality_daily WHERE date BETWEEN '2026-08-21' "
            "AND '2026-08-27' ORDER BY date ASC, agent_id ASC, tool_name ASC LIMIT 200",
        ),
        (
            DataAnalysisBranch(
                branch_id="tool-top",
                semantic_view="analytics_tool_quality_daily",
                dimensions=["tool_name"],
                metrics=["failure_count"],
                start_date=date(2026, 8, 21),
                end_date=date(2026, 8, 27),
                order_by=[
                    SemanticOrder(column="failure_count", direction="DESC"),
                    SemanticOrder(column="tool_name"),
                ],
                top_k=1,
            ),
            "SELECT tool_name, SUM(failure_count) AS failure_count FROM "
            "analytics_tool_quality_daily WHERE date BETWEEN '2026-08-21' AND '2026-08-27' "
            "GROUP BY tool_name ORDER BY failure_count DESC, tool_name ASC LIMIT 1",
        ),
        (
            DataAnalysisBranch(
                branch_id="tool-aggregate",
                semantic_view="analytics_tool_quality_daily",
                dimensions=["tool_name"],
                metrics=["call_count", "success_count", "failure_count"],
                start_date=date(2026, 8, 21),
                end_date=date(2026, 8, 27),
                order_by=[SemanticOrder(column="tool_name")],
            ),
            "SELECT tool_name, SUM(call_count) AS call_count, SUM(success_count) AS "
            "success_count, SUM(failure_count) AS failure_count FROM analytics_tool_quality_daily "
            "WHERE date BETWEEN '2026-08-21' AND '2026-08-27' GROUP BY tool_name ORDER BY "
            "tool_name ASC LIMIT 200",
        ),
        (
            DataAnalysisBranch(
                branch_id="tool-total",
                semantic_view="analytics_tool_quality_daily",
                dimensions=[],
                metrics=["call_count", "success_count", "failure_count"],
                start_date=date(2026, 8, 21),
                end_date=date(2026, 8, 27),
            ),
            "SELECT SUM(call_count) AS call_count, SUM(success_count) AS success_count, "
            "SUM(failure_count) AS failure_count FROM analytics_tool_quality_daily WHERE date "
            "BETWEEN '2026-08-21' AND '2026-08-27' LIMIT 200",
        ),
    ],
)
def test_quality_semantic_compiler_is_deterministic(branch, expected_sql):
    assert _compile_branch_sql(branch) == expected_sql


def test_quality_or_equivalent_stays_inside_sql_guard():
    question = "2026-08-21 到 2026-08-27 哪些商品出现过低分评价或售后联系事件？"
    plan = _normalize_supply_chain_plan(question, DataAnalysisPlan(), end=date(2026, 8, 27))
    branch = plan.branches[0]
    from app.services.sql_guard import validate_sql

    guarded = validate_sql(
        _compile_branch_sql(branch),
        expected_view=branch.semantic_view,
        expected_start_date=branch.start_date,
        expected_end_date=branch.end_date,
    )
    assert guarded.allowed


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "列出 2026-08-27 当前所有缺货 SKU。",
            [
                (
                    "analytics_inventory_risk",
                    ("stock",),
                    (
                        "snapshot_date",
                        "product_id",
                        "product_name",
                        "property_value_id_hash",
                        "risk_level",
                    ),
                    (("stock", "LTE", 0, None),),
                    (),
                    200,
                )
            ],
        ),
        (
            "2026-08-27 当前缺货 SKU 有多少个？",
            [
                (
                    "analytics_inventory_risk",
                    ("stockout_sku_count",),
                    (),
                    (),
                    (),
                    200,
                )
            ],
        ),
        (
            "列出 2026-08-27 当前低库存但未缺货的 SKU，按库存从少到多排序。",
            [
                (
                    "analytics_inventory_risk",
                    ("stock",),
                    ("product_id", "product_name", "property_value_id_hash", "risk_level"),
                    (("stock", "BETWEEN", 1, 10),),
                    (("stock", "ASC"),),
                    200,
                )
            ],
        ),
        (
            "2026-08-27 当前各库存风险等级分别有多少个 SKU？",
            [
                (
                    "analytics_inventory_risk",
                    ("stockout_sku_count",),
                    ("risk_level",),
                    (),
                    (),
                    200,
                )
            ],
        ),
        (
            "列出 2026-08-27 当前库存最多的 3 个 SKU，并列按商品 ID 和 SKU 哈希排序。",
            [
                (
                    "analytics_inventory_risk",
                    ("stock",),
                    ("product_id", "property_value_id_hash"),
                    (),
                    (("stock", "DESC"),),
                    3,
                )
            ],
        ),
        (
            "列出 2026-08-27 当前所有 SKU 的库存预测输入和人工补货建议量。",
            [
                (
                    "analytics_inventory_forecast",
                    (
                        "current_stock",
                        "inbound_quantity",
                        "ewma_daily_demand",
                        "lead_time_days",
                        "safety_stock",
                        "min_order_quantity",
                        "review_period_days",
                        "suggested_replenish_quantity",
                    ),
                    ("snapshot_date", "product_id", "product_name", "sku_key"),
                    (),
                    (),
                    200,
                )
            ],
        ),
        (
            "2026-08-27 人工建议补货量最高的 3 个 SKU 是哪些？",
            [
                (
                    "analytics_inventory_forecast",
                    ("suggested_replenish_quantity",),
                    ("product_id", "product_name", "sku_key"),
                    (),
                    (("suggested_replenish_quantity", "DESC"),),
                    3,
                )
            ],
        ),
        (
            "列出 2026-08-27 当前预测覆盖天数低于 7 天的 SKU。",
            [
                (
                    "analytics_inventory_forecast",
                    ("coverage_days",),
                    ("product_id", "product_name", "sku_key"),
                    (("coverage_days", "LT", 7.0, None),),
                    (("coverage_days", "ASC"),),
                    200,
                )
            ],
        ),
        (
            "列出 2026-08-27 当前没有可计算覆盖天数的 SKU，并展示日均需求和数据覆盖度。",
            [
                (
                    "analytics_inventory_forecast",
                    ("coverage_days", "ewma_daily_demand", "confidence"),
                    ("product_id", "product_name", "sku_key"),
                    (("coverage_days", "IS_NULL", None, None),),
                    (),
                    200,
                )
            ],
        ),
        (
            "对照 2026-08-27 的人工补货建议与当前库存风险；分别返回两张表，不做跨视图 Join。",
            [
                (
                    "analytics_inventory_forecast",
                    ("current_stock", "suggested_replenish_quantity", "confidence"),
                    ("product_id", "sku_key"),
                    (),
                    (),
                    200,
                ),
                (
                    "analytics_inventory_risk",
                    ("stock",),
                    ("product_id", "property_value_id_hash", "risk_level"),
                    (),
                    (),
                    200,
                ),
            ],
        ),
    ],
)
def test_supply_chain_plan_normalizer_fills_explicit_slots(question, expected):
    raw = DataAnalysisPlan(
        interpretation="model plan",
        branches=[
            DataAnalysisBranch(
                branch_id="model-branch",
                semantic_view="analytics_inventory_risk",
                dimensions=["product_id", "property_value_id_hash"],
                metrics=["stock"],
                start_date=date(2026, 8, 20),
                end_date=date(2026, 8, 26),
                filters=[
                    SemanticFilter(
                        column="snapshot_date",
                        operator="EQ",
                        value="2026-08-26",
                    )
                ],
            )
        ],
    )

    plan = _normalize_supply_chain_plan(question, raw, end=date(2026, 8, 27))
    actual = [
        (
            branch.semantic_view,
            tuple(branch.metrics),
            tuple(branch.dimensions),
            tuple(
                (item.column, item.operator, item.value, item.second_value)
                for item in branch.filters
            ),
            tuple((item.column, item.direction) for item in branch.order_by),
            branch.top_k,
        )
        for branch in plan.branches
    ]

    assert actual == expected
    assert all(branch.start_date == branch.end_date == date(2026, 8, 27) for branch in plan.branches)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "列出 2026-08-21 到 2026-08-27 每天每个商品的 VERIFIED 推荐结果事件计数。",
            [("recommendation_quality", "analytics_recommendation_quality_daily", ["date", "product_id"], [
                "payment_count", "refund_count", "return_count", "negative_review_count",
                "support_contact_count", "repeat_purchase_count",
            ], [], [("date", "ASC"), ("product_id", "ASC")], 200)],
        ),
        (
            "2026-08-21 到 2026-08-27 各商品的 VERIFIED 退款和退货事件数是多少？",
            [("recommendation_quality", "analytics_recommendation_quality_daily", ["product_id"], ["refund_count", "return_count"], [], [("product_id", "ASC")], 200)],
        ),
        (
            "2026-08-21 到 2026-08-27 哪些商品出现过低分评价或售后联系事件？",
            [("recommendation_quality", "analytics_recommendation_quality_daily", ["product_id"], ["negative_review_count", "support_contact_count"], [
                ("negative_review_count", "GT", 0), ("support_contact_count", "GT", 0),
            ], [("product_id", "ASC")], 200)],
        ),
        (
            "导出 2026-08-21 到 2026-08-27 各商品 VERIFIED 支付与复购事件数。",
            [("recommendation_quality", "analytics_recommendation_quality_daily", ["product_id"], ["payment_count", "repeat_purchase_count"], [], [("product_id", "ASC")], 200)],
        ),
        (
            "列出 2026-08-21 到 2026-08-27 每天各 Agent 和工具的调用数、成功数、失败数与平均延迟。",
            [("tool_quality", "analytics_tool_quality_daily", ["date", "agent_id", "tool_name"], ["call_count", "success_count", "failure_count", "avg_latency_ms"], [], [("date", "ASC"), ("agent_id", "ASC"), ("tool_name", "ASC")], 200)],
        ),
        (
            "2026-08-21 到 2026-08-27 哪个工具的失败调用数最多？",
            [("tool_quality", "analytics_tool_quality_daily", ["tool_name"], ["failure_count"], [], [("failure_count", "DESC"), ("tool_name", "ASC")], 1)],
        ),
        (
            "2026-08-21 到 2026-08-27 各工具的调用数、成功数和失败数是多少？",
            [("tool_quality", "analytics_tool_quality_daily", ["tool_name"], ["call_count", "success_count", "failure_count"], [], [("tool_name", "ASC")], 200)],
        ),
        (
            "汇总 2026-08-21 到 2026-08-27 的工具调用与 Agent 运行质量；如果 Agent 分支超时，返回工具分支并标记部分完成。",
            [
                ("tool_quality", "analytics_tool_quality_daily", [], ["call_count", "success_count", "failure_count"], [], [], 200),
                ("agent_quality", "analytics_agent_quality_daily", [], ["run_count", "success_count", "failure_count", "human_handoff_count"], [], [], 200),
            ],
        ),
    ],
)
def test_quality_plan_normalizer_fills_explicit_slots(question, expected):
    plan = _normalize_supply_chain_plan(question, DataAnalysisPlan(), end=date(2026, 8, 27))
    actual = [
        (
            branch.branch_id,
            branch.semantic_view,
            branch.dimensions,
            branch.metrics,
            [(item.column, item.operator, item.value) for item in branch.filters],
            [(item.column, item.direction) for item in branch.order_by],
            branch.top_k,
        )
        for branch in plan.branches
    ]
    assert actual == expected
    assert all(
        branch.start_date == date(2026, 8, 21) and branch.end_date == date(2026, 8, 27)
        for branch in plan.branches
    )


def test_supply_chain_plan_normalizer_removes_duplicate_snapshot_filter():
    plan = DataAnalysisPlan(
        branches=[
            DataAnalysisBranch(
                branch_id="risk",
                semantic_view="analytics_inventory_risk",
                metrics=["stock"],
                dimensions=["product_id", "property_value_id_hash"],
                filters=[
                    SemanticFilter(column="snapshot_date", operator="EQ", value="2026-08-27"),
                    SemanticFilter(column="risk_level", operator="EQ", value="NORMAL"),
                ],
            )
        ]
    )

    normalized = _normalize_supply_chain_plan(
        "列出当前 NORMAL 风险 SKU",
        plan,
        end=date(2026, 8, 27),
    )

    assert [item.column for item in normalized.branches[0].filters] == ["risk_level"]


def test_question_policy_allows_explicit_no_join_multi_table_request():
    assert (
        evaluate_question_policy(
            "分别返回补货建议与库存风险两张表，不做跨视图 Join。",
            tenant_id=None,
        )
        is None
    )


@pytest.mark.parametrize(
    ("question", "expected_facts"),
    [
        (
            "列出 2026-08-27 当前所有缺货 SKU。",
            {"current snapshot only", "stock<=0 definition", "SKU hash"},
        ),
        (
            "列出 2026-08-27 当前低库存但未缺货的 SKU，按库存从少到多排序。",
            {"low stock definition: 1..10", "exclude stock<=0", "stable ascending order"},
        ),
        (
            "列出 2026-08-27 当前库存最多的 3 个 SKU。",
            {"top 3", "tie-break: product_id then SKU hash"},
        ),
        (
            "列出 2026-08-27 当前所有 SKU 的库存预测输入和人工补货建议量。",
            {
                "28-day EWMA demand input",
                "lead time and safety stock",
                "MOQ and review period",
                "human replenishment suggestion",
                "data coverage boundary",
                "not a purchase instruction",
            },
        ),
        (
            "2026-08-27 人工建议补货量最高的 3 个 SKU 是哪些？",
            {"top 3", "tie-break: product_id then sku_key"},
        ),
        (
            "列出 2026-08-27 当前预测覆盖天数低于 7 天的 SKU。",
            {"exclude NULL coverage", "human planning input", "not a stockout probability"},
        ),
        (
            "列出当前没有可计算覆盖天数的 SKU，并展示日均需求和数据覆盖度。",
            {"confidence means data coverage", "not statistical confidence"},
        ),
        (
            "对照人工补货建议与当前库存风险；分别返回两张表，不做跨视图 Join。",
            {"two separate result tables", "no cross-view join"},
        ),
    ],
)
def test_supply_chain_disclosures_cover_compiled_semantics(question, expected_facts):
    raw = DataAnalysisPlan(
        branches=[
            DataAnalysisBranch(
                branch_id="model",
                semantic_view="analytics_inventory_risk",
                metrics=["stock"],
                dimensions=["product_id", "property_value_id_hash"],
            )
        ]
    )
    plan = _normalize_supply_chain_plan(question, raw, end=date(2026, 8, 27))

    facts, statements = _supply_chain_disclosures(plan)

    assert expected_facts.issubset(facts)
    assert statements


@pytest.mark.parametrize(
    ("question", "expected_facts"),
    [
        (
            "列出 2026-08-21 到 2026-08-27 每天每个商品的 VERIFIED 推荐结果事件计数。",
            {"all VERIFIED result event counts", "VERIFIED event-day attribution"},
        ),
        (
            "2026-08-21 到 2026-08-27 各商品的 VERIFIED 退款和退货事件数是多少？",
            {"VERIFIED refund events", "VERIFIED return events"},
        ),
        (
            "2026-08-21 到 2026-08-27 哪些商品出现过低分评价或售后联系事件？",
            {"OR filter", "VERIFIED event-day attribution"},
        ),
        (
            "导出 2026-08-21 到 2026-08-27 各商品 VERIFIED 支付与复购事件数。",
            {"VERIFIED payment events", "VERIFIED repeat-purchase events", "export requires ANALYTICS_EXPORT"},
        ),
        (
            "2026-08-21 到 2026-08-27 哪个工具的失败调用数最多？",
            {"technical call status", "top 1", "stable tie-break"},
        ),
    ],
)
def test_quality_disclosures_cover_compiled_semantics(question, expected_facts):
    plan = _normalize_supply_chain_plan(question, DataAnalysisPlan(), end=date(2026, 8, 27))
    facts, statements = _quality_disclosures(plan)

    assert expected_facts.issubset(facts)
    assert statements


def test_quality_disclosures_report_partial_timeout_only_from_observed_branch():
    question = (
        "汇总 2026-08-21 到 2026-08-27 的工具调用与 Agent 运行质量；"
        "如果 Agent 分支超时，返回工具分支并标记部分完成。"
    )
    plan = _normalize_supply_chain_plan(question, DataAnalysisPlan(), end=date(2026, 8, 27))
    facts, _ = _quality_disclosures(
        plan,
        result={
            "completion": "PARTIAL",
            "branches": [
                {"branchId": "tool_quality", "status": "SUCCEEDED"},
                {"branchId": "agent_quality", "status": "QUERY_TIMEOUT"},
            ],
        },
    )

    assert {
        "tool branch succeeds",
        "agent branch timeout",
        "partial completion",
        "technical status only",
    }.issubset(facts)


@pytest.mark.asyncio
async def test_supply_chain_compiler_never_calls_free_sql_model(monkeypatch):
    service = DataAnalystService()
    plan = DataAnalysisPlan(
        interpretation="当前缺货明细",
        branches=[
            DataAnalysisBranch(
                branch_id="stockout-details",
                semantic_view="analytics_inventory_risk",
                dimensions=["product_id", "property_value_id_hash", "risk_level"],
                metrics=["stock"],
                start_date=date(2026, 8, 27),
                end_date=date(2026, 8, 27),
                filters=[SemanticFilter(column="stock", operator="LTE", value=0)],
            )
        ],
    )
    draft = AsyncMock(side_effect=AssertionError("covered view must not draft SQL"))
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)
    monkeypatch.setattr(service, "_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(service, "_draft_sql", draft)
    monkeypatch.setattr(
        "app.services.data_analyst_service._explain_sql", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "app.services.data_analyst_service._execute_sql",
        AsyncMock(
            return_value=[
                {
                    "product_id": "P100",
                    "property_value_id_hash": "SKU-P100-A",
                    "risk_level": "OUT_OF_STOCK",
                    "stock": 0,
                }
            ]
        ),
    )
    events = _stub_episode(monkeypatch)

    result = await service.ask("列出当前缺货 SKU", admin_id="admin")

    assert result["outcome"] == "ANSWER"
    assert result["sqlSource"] == "DETERMINISTIC_COMPILER"
    assert "stock <= 0" in result["sql"]
    assert "DATA_ANALYST_SQL_COMPILE" in events
    draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_quality_compiler_never_calls_free_sql_model(monkeypatch):
    service = DataAnalystService()
    question = "2026-08-21 到 2026-08-27 各工具的调用数、成功数和失败数是多少？"
    plan = _normalize_supply_chain_plan(question, DataAnalysisPlan(), end=date(2026, 8, 27))
    draft = AsyncMock(side_effect=AssertionError("covered view must not draft SQL"))
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)
    monkeypatch.setattr(service, "_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(service, "_draft_sql", draft)
    monkeypatch.setattr(
        "app.services.data_analyst_service._explain_sql", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "app.services.data_analyst_service._execute_sql",
        AsyncMock(
            return_value=[
                {
                    "tool_name": "inventory",
                    "call_count": 3,
                    "success_count": 2,
                    "failure_count": 1,
                }
            ]
        ),
    )
    _stub_episode(monkeypatch)

    result = await service.ask(question, admin_id="admin")

    assert result["outcome"] == "ANSWER"
    assert result["sqlSource"] == "DETERMINISTIC_COMPILER"
    assert "SUM(call_count)" in result["sql"]
    draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsupported_supply_plan_abstains_without_sql_fallback(monkeypatch):
    service = DataAnalystService()
    plan = DataAnalysisPlan(
        interpretation="按商品聚合非加总预测覆盖度",
        branches=[
            DataAnalysisBranch(
                branch_id="unsupported",
                semantic_view="analytics_inventory_forecast",
                dimensions=["product_id"],
                metrics=["confidence"],
                start_date=date(2026, 8, 27),
                end_date=date(2026, 8, 27),
            )
        ],
    )
    draft = AsyncMock(side_effect=AssertionError("unsupported plan must not draft SQL"))
    query = AsyncMock(side_effect=AssertionError("unsupported plan must not query"))
    monkeypatch.setattr("app.services.data_analyst_service.get_settings", _settings)
    monkeypatch.setattr(service, "_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(service, "_draft_sql", draft)
    monkeypatch.setattr("app.services.data_analyst_service._execute_sql", query)
    events = _stub_episode(monkeypatch)

    result = await service.ask("按商品汇总预测置信度", admin_id="admin")

    assert result["outcome"] == "ABSTAIN"
    assert result["reasonCode"] == "SEMANTIC_PLAN_UNSUPPORTED"
    assert result["queryExecuted"] is False
    assert "不会回退到自由 SQL" in result["answer"]
    assert events == ["DATA_ANALYST_PLAN", "DATA_ANALYST_SQL_COMPILE"]
    draft.assert_not_awaited()
    query.assert_not_awaited()
