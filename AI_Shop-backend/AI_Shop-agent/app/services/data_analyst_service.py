from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from numbers import Number
from typing import Any, Iterable, Literal
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator, model_validator

from app.config.settings import get_settings
from app.db.analytics_pool import acquire_analytics, acquire_analytics_snapshot
from app.observability.llm_metrics import invoke_llm_with_metrics
from app.services.analytics_catalog import (
    CATALOG,
    CATALOG_CONTENT_SHA256,
    CATALOG_TIMEZONE,
    CATALOG_VERSION,
    allowed_plan_fields,
    catalog_prompt,
    disclosure_contract,
)
from app.services.analytics_clarification_service import analytics_clarification_service
from app.services.analytics_policy import evaluate_question_policy
from app.services.analytics_result_service import (
    AnalyticsResultError,
    analytics_result_service,
    normalize_typed_rows,
)
from app.services.analytics_semantic_compiler import (
    QUALITY_COMPILER_VIEWS,
    SUPPLY_CHAIN_COMPILER_VIEWS,
    SemanticFilter,
    SemanticOrder,
    SemanticPlanUnsupported,
    compile_supply_chain_sql,
)
from app.services.episode_service import bind_episode, episode_service
from app.services.llm_factory import create_memory_llm
from app.services.sql_guard import (
    AnalyticsAccessPolicy,
    SqlGuardResult,
    validate_sql,
)

_EXPLAIN_VIEW_PRIVILEGE_ERROR = 1345
_EXPLAIN_VIEW_PRIVILEGE_REASON = "EXPLAIN_UNAVAILABLE_VIEW_PRIVILEGE"
_DATA_ANALYST_VERSION = "v3-quality-compiler"


class DataAnalysisBranch(BaseModel):
    branch_id: str = Field(min_length=1, max_length=64)
    purpose: str = Field(default="", max_length=300)
    semantic_view: str
    metrics: list[str] = Field(default_factory=list, min_length=1, max_length=8)
    dimensions: list[str] = Field(default_factory=list, max_length=5)
    start_date: date | None = None
    end_date: date | None = None
    filters: list[SemanticFilter] = Field(default_factory=list, max_length=5)
    order_by: list[SemanticOrder] = Field(default_factory=list, max_length=4)
    top_k: int = Field(default=200, ge=1, le=200)


class ClarificationOption(BaseModel):
    choice_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=100)
    answer_suffix: str = Field(min_length=1, max_length=200)


class DataAnalysisPlan(BaseModel):
    status: Literal["READY", "NEEDS_CLARIFICATION"] = "READY"
    semantic_view: str | None = None
    metrics: list[str] = Field(default_factory=list, max_length=8)
    dimensions: list[str] = Field(default_factory=list, max_length=5)
    start_date: date | None = None
    end_date: date | None = None
    interpretation: str = ""
    clarification_question: str | None = None
    clarification_options: list[ClarificationOption] = Field(default_factory=list, max_length=8)
    branches: list[DataAnalysisBranch] = Field(default_factory=list, max_length=3)

    @field_validator("metrics", "dimensions", mode="before")
    @classmethod
    def normalize_nullable_compatibility_lists(cls, value):
        # Structured-output providers sometimes emit null for unused legacy
        # top-level fields while supplying the canonical branches array.
        return [] if value is None else value

    @model_validator(mode="after")
    def normalize_single_branch(self) -> "DataAnalysisPlan":
        """Keep one canonical branch list while accepting the original shape."""
        if self.status != "READY":
            if not str(self.clarification_question or "").strip():
                raise ValueError("clarification_question is required")
            choice_ids = [option.choice_id for option in self.clarification_options]
            if len(choice_ids) < 2 or len(set(choice_ids)) != len(choice_ids):
                raise ValueError("at least two unique clarification options are required")
            self.branches = []
            return self
        self.clarification_question = None
        self.clarification_options = []
        if not self.branches and self.semantic_view and self.metrics:
            self.branches = [
                DataAnalysisBranch(
                    branch_id="metric-1",
                    purpose=self.interpretation,
                    semantic_view=self.semantic_view,
                    metrics=self.metrics,
                    dimensions=self.dimensions,
                    start_date=self.start_date,
                    end_date=self.end_date,
                )
            ]
        elif self.branches:
            first = self.branches[0]
            self.semantic_view = first.semantic_view
            self.metrics = list(first.metrics)
            self.dimensions = list(first.dimensions)
            self.start_date = first.start_date
            self.end_date = first.end_date
        return self


class SqlDraft(BaseModel):
    sql: str


class DataNarrative(BaseModel):
    answer: str
    highlights: list[str] = Field(default_factory=list, max_length=5)


def _compile_branch_sql(branch: DataAnalysisBranch) -> str | None:
    return compile_supply_chain_sql(
        view=branch.semantic_view,
        metrics=branch.metrics,
        dimensions=branch.dimensions,
        start_date=branch.start_date,
        end_date=branch.end_date,
        filters=branch.filters,
        order_by=branch.order_by,
        top_k=branch.top_k,
    )


def _normalize_supply_chain_plan(
    question: str,
    plan: DataAnalysisPlan,
    *,
    end: date,
) -> DataAnalysisPlan:
    """Fill high-confidence inventory and quality slots before compilation."""
    text = str(question or "").strip()

    def branch(
        view: str,
        metrics: list[str],
        dimensions: list[str],
        *,
        filters: list[SemanticFilter] | None = None,
        order_by: list[SemanticOrder] | None = None,
        top_k: int = 200,
        branch_id: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> DataAnalysisBranch:
        return DataAnalysisBranch(
            branch_id=branch_id or view.removeprefix("analytics_"),
            purpose=text,
            semantic_view=view,
            metrics=metrics,
            dimensions=dimensions,
            start_date=start_date or end,
            end_date=end_date or end,
            filters=filters or [],
            order_by=order_by or [],
            top_k=top_k,
        )

    quality_start, quality_end = end - timedelta(days=6), end
    date_match = re.search(
        r"(?P<start>\d{4}-\d{2}-\d{2})\s*(?:到|至|~|～|—)\s*"
        r"(?P<end>\d{4}-\d{2}-\d{2})",
        text,
    )
    if date_match:
        try:
            quality_start = date.fromisoformat(date_match.group("start"))
            quality_end = date.fromisoformat(date_match.group("end"))
        except ValueError:
            quality_start, quality_end = end - timedelta(days=6), end

    def quality_branch(
        view: str,
        metrics: list[str],
        dimensions: list[str],
        *,
        branch_id: str,
        filters: list[SemanticFilter] | None = None,
        order_by: list[SemanticOrder] | None = None,
        top_k: int = 200,
    ) -> DataAnalysisBranch:
        return branch(
            view,
            metrics,
            dimensions,
            branch_id=branch_id,
            filters=filters,
            order_by=order_by,
            top_k=top_k,
            start_date=quality_start,
            end_date=quality_end,
        )

    branches: list[DataAnalysisBranch] | None = None
    upper = text.upper()
    if "工具调用与 AGENT 运行质量" in upper and "分支超时" in text:
        branches = [
            quality_branch(
                "analytics_tool_quality_daily",
                ["call_count", "success_count", "failure_count"],
                [],
                branch_id="tool_quality",
            ),
            branch(
                "analytics_agent_quality_daily",
                ["run_count", "success_count", "failure_count", "human_handoff_count"],
                [],
                branch_id="agent_quality",
                start_date=quality_start,
                end_date=quality_end,
            ),
        ]
    elif "VERIFIED" in upper and "推荐结果事件" in text and "每天" in text and "商品" in text:
        branches = [
            quality_branch(
                "analytics_recommendation_quality_daily",
                [
                    "payment_count",
                    "refund_count",
                    "return_count",
                    "negative_review_count",
                    "support_contact_count",
                    "repeat_purchase_count",
                ],
                ["date", "product_id"],
                branch_id="recommendation_quality",
                order_by=[
                    SemanticOrder(column="date"),
                    SemanticOrder(column="product_id"),
                ],
            )
        ]
    elif "VERIFIED" in upper and "退款" in text and "退货" in text and "商品" in text:
        branches = [
            quality_branch(
                "analytics_recommendation_quality_daily",
                ["refund_count", "return_count"],
                ["product_id"],
                branch_id="recommendation_quality",
                order_by=[SemanticOrder(column="product_id")],
            )
        ]
    elif "低分评价" in text and "售后联系" in text and "商品" in text:
        branches = [
            quality_branch(
                "analytics_recommendation_quality_daily",
                ["negative_review_count", "support_contact_count"],
                ["product_id"],
                branch_id="recommendation_quality",
                filters=[
                    SemanticFilter(
                        column="negative_review_count", operator="GT", value=0
                    ),
                    SemanticFilter(column="support_contact_count", operator="GT", value=0),
                ],
                order_by=[SemanticOrder(column="product_id")],
            )
        ]
    elif "导出" in text and "VERIFIED" in upper and "支付" in text and "复购" in text:
        branches = [
            quality_branch(
                "analytics_recommendation_quality_daily",
                ["payment_count", "repeat_purchase_count"],
                ["product_id"],
                branch_id="recommendation_quality",
                order_by=[SemanticOrder(column="product_id")],
            )
        ]
    elif "平均延迟" in text and "工具" in text and "AGENT" in upper:
        branches = [
            quality_branch(
                "analytics_tool_quality_daily",
                ["call_count", "success_count", "failure_count", "avg_latency_ms"],
                ["date", "agent_id", "tool_name"],
                branch_id="tool_quality",
                order_by=[
                    SemanticOrder(column="date"),
                    SemanticOrder(column="agent_id"),
                    SemanticOrder(column="tool_name"),
                ],
            )
        ]
    elif "失败调用数最多" in text and "工具" in text:
        branches = [
            quality_branch(
                "analytics_tool_quality_daily",
                ["failure_count"],
                ["tool_name"],
                branch_id="tool_quality",
                order_by=[
                    SemanticOrder(column="failure_count", direction="DESC"),
                    SemanticOrder(column="tool_name"),
                ],
                top_k=1,
            )
        ]
    elif "各工具" in text and "调用数" in text and "成功数" in text and "失败数" in text:
        branches = [
            quality_branch(
                "analytics_tool_quality_daily",
                ["call_count", "success_count", "failure_count"],
                ["tool_name"],
                branch_id="tool_quality",
                order_by=[SemanticOrder(column="tool_name")],
            )
        ]
    elif (
        "补货建议" in text
        and "库存风险" in text
        and ("两张表" in text or "分别返回" in text)
    ):
        branches = [
            branch(
                "analytics_inventory_forecast",
                ["current_stock", "suggested_replenish_quantity", "confidence"],
                ["product_id", "sku_key"],
            ),
            branch(
                "analytics_inventory_risk",
                ["stock"],
                ["product_id", "property_value_id_hash", "risk_level"],
            ),
        ]
    elif "库存风险等级" in text and re.search(r"(多少|分别|数量|统计)", text):
        branches = [
            branch(
                "analytics_inventory_risk",
                ["stockout_sku_count"],
                ["risk_level"],
            )
        ]
    elif "低库存" in text and re.search(r"(未缺货|不缺货)", text):
        branches = [
            branch(
                "analytics_inventory_risk",
                ["stock"],
                ["product_id", "product_name", "property_value_id_hash", "risk_level"],
                filters=[
                    SemanticFilter(column="stock", operator="BETWEEN", value=1, second_value=10)
                ],
                order_by=[SemanticOrder(column="stock")],
            )
        ]
    elif re.search(r"缺货\s*SKU.*(?:多少|几个|数量|数)", text, re.IGNORECASE):
        branches = [
            branch("analytics_inventory_risk", ["stockout_sku_count"], [])
        ]
    elif re.search(r"缺货\s*SKU", text, re.IGNORECASE):
        branches = [
            branch(
                "analytics_inventory_risk",
                ["stock"],
                [
                    "snapshot_date",
                    "product_id",
                    "product_name",
                    "property_value_id_hash",
                    "risk_level",
                ],
                filters=[SemanticFilter(column="stock", operator="LTE", value=0)],
            )
        ]
    elif match := re.search(r"库存最多的?\s*(\d+)\s*个\s*SKU", text, re.IGNORECASE):
        branches = [
            branch(
                "analytics_inventory_risk",
                ["stock"],
                ["product_id", "property_value_id_hash"],
                order_by=[SemanticOrder(column="stock", direction="DESC")],
                top_k=min(200, int(match.group(1))),
            )
        ]
    elif "SKU" in text.upper() and "预测输入" in text and re.search(r"补货建议|建议量", text):
        branches = [
            branch(
                "analytics_inventory_forecast",
                [
                    "current_stock",
                    "inbound_quantity",
                    "ewma_daily_demand",
                    "lead_time_days",
                    "safety_stock",
                    "min_order_quantity",
                    "review_period_days",
                    "suggested_replenish_quantity",
                ],
                ["snapshot_date", "product_id", "product_name", "sku_key"],
            )
        ]
    elif match := re.search(
        r"(?:建议补货量|补货建议量|补货量)最高的?\s*(\d+)\s*个\s*SKU",
        text,
        re.IGNORECASE,
    ):
        branches = [
            branch(
                "analytics_inventory_forecast",
                ["suggested_replenish_quantity"],
                ["product_id", "product_name", "sku_key"],
                order_by=[
                    SemanticOrder(column="suggested_replenish_quantity", direction="DESC")
                ],
                top_k=min(200, int(match.group(1))),
            )
        ]
    elif "覆盖天数" in text and re.search(r"(没有|无|不可计算|为空|NULL)", text, re.IGNORECASE):
        branches = [
            branch(
                "analytics_inventory_forecast",
                ["coverage_days", "ewma_daily_demand", "confidence"],
                ["product_id", "product_name", "sku_key"],
                filters=[SemanticFilter(column="coverage_days", operator="IS_NULL")],
            )
        ]
    elif match := re.search(
        r"覆盖天数(?:低于|小于)\s*(\d+(?:\.\d+)?)\s*天",
        text,
    ):
        branches = [
            branch(
                "analytics_inventory_forecast",
                ["coverage_days"],
                ["product_id", "product_name", "sku_key"],
                filters=[
                    SemanticFilter(
                        column="coverage_days",
                        operator="LT",
                        value=float(match.group(1)),
                    )
                ],
                order_by=[SemanticOrder(column="coverage_days")],
            )
        ]

    if branches is not None:
        return DataAnalysisPlan(
            interpretation=plan.interpretation or text,
            branches=branches,
        )
    for item in plan.branches:
        if item.semantic_view in SUPPLY_CHAIN_COMPILER_VIEWS:
            item.filters = [entry for entry in item.filters if entry.column != "snapshot_date"]
    return plan


_AMBIGUOUS_SALES = re.compile(r"(销量最高|最畅销|最好卖|销售最好|卖(?:得|的)?最好)")
_EXPLICIT_SALES_METRIC = re.compile(
    r"(销售额|金额|件数|数量|订单数|订单量|paid_units|gross_item_amount|paid_order_count)"
)
_CAUSAL_QUESTION = re.compile(r"(为什么|原因|导致|归因|怎么下降|为何|影响因素)")
_CAUSAL_CAUTION = "相关性不等于因果关系；以下结果只用于定位待验证假设。"
_DEFAULT_ANALYTICS_PAGE_SIZE = 50


def _catalog_clarification_option(
    choice_id: str,
    label: str,
    answer_suffix: str,
    *,
    view: str,
    fields: tuple[str, ...],
) -> ClarificationOption:
    if view not in CATALOG or not set(fields).issubset(allowed_plan_fields(view)):
        raise RuntimeError(f"invalid catalog clarification option: {choice_id}")
    return ClarificationOption(
        choice_id=choice_id,
        label=label,
        answer_suffix=answer_suffix,
    )


def _catalog_clarification(question: str, end: date) -> DataAnalysisPlan | None:
    """Return stable choices whose fields are all present in the governed catalog."""
    normalized = str(question or "").strip()
    if "（已确认：" in normalized:
        return None
    start_7 = end - timedelta(days=6)
    start_28 = end - timedelta(days=27)
    day_7 = f"{start_7.isoformat()} 至 {end.isoformat()}"
    day_28 = f"{start_28.isoformat()} 至 {end.isoformat()}"

    if _AMBIGUOUS_SALES.search(normalized) and not _EXPLICIT_SALES_METRIC.search(normalized):
        return DataAnalysisPlan(
            status="NEEDS_CLARIFICATION",
            clarification_question="“最近”和“最好卖”分别按哪个时间范围与指标定义？",
            clarification_options=[
                _catalog_clarification_option(
                    "LAST_7D_PAID_UNITS",
                    "最近 7 天按支付件数",
                    f"按 {day_7} 的 paid_units 排序，返回前 10 个商品。",
                    view="analytics_product_sales_daily",
                    fields=("product_id", "paid_units"),
                ),
                _catalog_clarification_option(
                    "LAST_28D_PAID_UNITS",
                    "最近 28 天按支付件数",
                    f"按 {day_28} 的 paid_units 排序，返回前 10 个商品。",
                    view="analytics_product_sales_daily",
                    fields=("product_id", "paid_units"),
                ),
                _catalog_clarification_option(
                    "LAST_7D_GROSS_ITEM_AMOUNT",
                    "最近 7 天按商品行金额",
                    f"按 {day_7} 的 gross_item_amount 排序，返回前 10 个商品。",
                    view="analytics_product_sales_daily",
                    fields=("product_id", "gross_item_amount"),
                ),
            ],
            interpretation="商品销售排名的时间范围和指标存在歧义",
        )

    if re.fullmatch(r"销售最近(?:怎么样|如何|情况如何)[。！？!?]?", normalized):
        fields = (
            "date",
            "paid_order_count",
            "gross_paid_amount",
            "completed_refund_amount",
            "net_paid_amount",
        )
        return DataAnalysisPlan(
            status="NEEDS_CLARIFICATION",
            clarification_question="你希望按哪种时间范围和粒度查看哪些销售运营指标？",
            clarification_options=[
                _catalog_clarification_option(
                    "LAST_7D_DAILY_CORE",
                    "最近 7 天逐日核心指标",
                    f"列出 {day_7} 每日支付订单数、支付总额、已完成退款额和净支付额。",
                    view="analytics_sales_daily",
                    fields=fields,
                ),
                _catalog_clarification_option(
                    "LAST_28D_TOTAL_CORE",
                    "最近 28 天核心指标汇总",
                    f"汇总 {day_28} 的支付订单数、支付总额、已完成退款额和净支付额。",
                    view="analytics_sales_daily",
                    fields=fields[1:],
                ),
            ],
            interpretation="销售运营指标的时间范围和粒度存在歧义",
        )

    if re.fullmatch(r"(?:看一下)?库存(?:有问题|异常|有风险)的商品[。！？!?]?", normalized):
        return DataAnalysisPlan(
            status="NEEDS_CLARIFICATION",
            clarification_question="“库存有问题”具体指当前缺货、当前低库存，还是人工补货建议？",
            clarification_options=[
                _catalog_clarification_option(
                    "CURRENT_OUT_OF_STOCK",
                    "仅当前缺货 SKU",
                    f"按 {end.isoformat()} 当前快照列出 stock<=0 的 SKU。",
                    view="analytics_inventory_risk",
                    fields=("snapshot_date", "stock", "risk_level"),
                ),
                _catalog_clarification_option(
                    "CURRENT_LOW_AND_OUT",
                    "低库存及缺货 SKU",
                    f"按 {end.isoformat()} 当前快照列出 stock<=10 的 SKU，并区分 OUT_OF_STOCK 与 LOW_STOCK。",
                    view="analytics_inventory_risk",
                    fields=("snapshot_date", "stock", "risk_level"),
                ),
                _catalog_clarification_option(
                    "REPLENISHMENT_SUGGESTED",
                    "人工补货建议",
                    f"按 {end.isoformat()} 预测快照列出 suggested_replenish_quantity>0 的 SKU，并说明它是人工建议、不是采购指令。",
                    view="analytics_inventory_forecast",
                    fields=("snapshot_date", "suggested_replenish_quantity"),
                ),
            ],
            interpretation="库存风险与补货建议属于不同受治理口径",
        )

    if re.fullmatch(r"哪个推荐渠道(?:效果|表现)最好[。！？!?]?", normalized):
        view = "analytics_recommendation_funnel_daily"
        return DataAnalysisPlan(
            status="NEEDS_CLARIFICATION",
            clarification_question="“效果最好”希望按哪项 VERIFIED 事件日指标比较检索模式？",
            clarification_options=[
                _catalog_clarification_option(
                    "LAST_7D_CLICK_RATE",
                    "最近 7 天事件日点击比率",
                    f"按 {day_7} 汇总后的 click_through_rate 比较 retrieval_mode。",
                    view=view,
                    fields=("retrieval_mode", "click_through_rate"),
                ),
                _catalog_clarification_option(
                    "LAST_7D_PAYMENT_RATE",
                    "最近 7 天事件日支付比率",
                    f"按 {day_7} 汇总后的 payment_rate 比较 retrieval_mode。",
                    view=view,
                    fields=("retrieval_mode", "payment_rate"),
                ),
                _catalog_clarification_option(
                    "LAST_7D_PAYMENT_COUNT",
                    "最近 7 天支付事件数",
                    f"按 {day_7} 的 VERIFIED payment_count 比较 retrieval_mode。",
                    view=view,
                    fields=("retrieval_mode", "payment_count"),
                ),
            ],
            interpretation="推荐渠道效果指标存在歧义",
        )

    if re.fullmatch(r"(?i)哪个\s*agent\s*(?:表现)?最差[。！？!?]?", normalized):
        view = "analytics_agent_quality_daily"
        return DataAnalysisPlan(
            status="NEEDS_CLARIFICATION",
            clarification_question="“表现最差”希望按哪项技术运行指标比较 Agent？",
            clarification_options=[
                _catalog_clarification_option(
                    "LAST_7D_FAILURE_COUNT",
                    "最近 7 天失败运行数",
                    f"按 {day_7} 的 failure_count 降序比较 Agent。",
                    view=view,
                    fields=("agent_id", "failure_count"),
                ),
                _catalog_clarification_option(
                    "LAST_7D_FAILURE_RATE",
                    "最近 7 天失败运行比率",
                    f"按 {day_7} 的 SUM(failure_count)/SUM(run_count) 降序比较 Agent。",
                    view=view,
                    fields=("agent_id", "failure_count", "run_count"),
                ),
                _catalog_clarification_option(
                    "LAST_7D_LATENCY",
                    "最近 7 天加权平均延迟",
                    f"按 {day_7} 以 run_count 加权的 avg_latency_ms 降序比较 Agent。",
                    view=view,
                    fields=("agent_id", "run_count", "avg_latency_ms"),
                ),
            ],
            interpretation="Agent 技术质量指标存在歧义",
        )

    if re.fullmatch(r"(?:汇总一下|看一下|分析一下)?退款情况[。！？!?]?", normalized):
        return DataAnalysisPlan(
            status="NEEDS_CLARIFICATION",
            clarification_question="你希望按哪种退款口径和粒度汇总？",
            clarification_options=[
                _catalog_clarification_option(
                    "LAST_7D_COMPLETED_REFUND_TOTAL",
                    "最近 7 天完成退款总量",
                    f"汇总 {day_7} 按退款完成日归属的完成退款单数与金额。",
                    view="analytics_fulfillment_after_sales_daily",
                    fields=("refund_completed_count", "refund_completed_amount"),
                ),
                _catalog_clarification_option(
                    "LAST_7D_REQUEST_AND_COMPLETION",
                    "最近 7 天申请与完成流程",
                    f"汇总 {day_7} 按视图混合日期口径的退款申请数、完成数与完成金额。",
                    view="analytics_fulfillment_after_sales_daily",
                    fields=(
                        "refund_request_count",
                        "refund_completed_count",
                        "refund_completed_amount",
                    ),
                ),
                _catalog_clarification_option(
                    "LAST_7D_PRODUCT_REFUNDED_UNITS",
                    "最近 7 天各商品退款件数",
                    f"按商品汇总 {day_7} 按退款完成日归属的 refunded_units。",
                    view="analytics_product_sales_daily",
                    fields=("product_id", "refunded_units"),
                ),
            ],
            interpretation="退款日期归属和汇总粒度存在歧义",
        )

    if re.fullmatch(r"最近履约情况(?:怎么样|如何)?[。！？!?]?", normalized):
        view = "analytics_fulfillment_after_sales_daily"
        return DataAnalysisPlan(
            status="NEEDS_CLARIFICATION",
            clarification_question="“最近履约情况”希望看哪段期间及哪组指标？",
            clarification_options=[
                _catalog_clarification_option(
                    "LAST_7D_ORDER_STATUS",
                    "最近 7 天订单状态计数",
                    f"列出 {day_7} 按订单创建日归属的支付、已发货、已完成和取消订单数。",
                    view=view,
                    fields=(
                        "date",
                        "paid_order_count",
                        "shipped_order_count",
                        "completed_order_count",
                        "cancelled_order_count",
                    ),
                ),
                _catalog_clarification_option(
                    "LAST_7D_AFTER_SALES",
                    "最近 7 天售后退款",
                    f"列出 {day_7} 的退款申请数、退款完成数与退款完成金额，并披露混合日期归属。",
                    view=view,
                    fields=(
                        "date",
                        "refund_request_count",
                        "refund_completed_count",
                        "refund_completed_amount",
                    ),
                ),
                _catalog_clarification_option(
                    "LAST_7D_FULL_VIEW",
                    "最近 7 天履约售后全量",
                    f"列出 {day_7} 履约售后视图的全部计数和退款完成金额。",
                    view=view,
                    fields=tuple(allowed_plan_fields(view)),
                ),
            ],
            interpretation="履约与售后指标组存在歧义",
        )

    if re.fullmatch(r"报价最好的商品是哪个[。！？!?]?", normalized):
        view = "analytics_offer_quality_daily"
        return DataAnalysisPlan(
            status="NEEDS_CLARIFICATION",
            clarification_question="“报价最好”希望按哪项报价快照指标比较商品？",
            clarification_options=[
                _catalog_clarification_option(
                    "LOWEST_ESTIMATED_PAYABLE",
                    "平均估算到手价最低",
                    f"按 {day_7} 以 quote_count 加权的 avg_estimated_payable 升序比较商品。",
                    view=view,
                    fields=("product_id", "quote_count", "avg_estimated_payable"),
                ),
                _catalog_clarification_option(
                    "MOST_COUPON_AVAILABLE",
                    "可用优惠快照数最多",
                    f"按 {day_7} 的 coupon_available_count 降序比较商品。",
                    view=view,
                    fields=("product_id", "coupon_available_count"),
                ),
                _catalog_clarification_option(
                    "MOST_IN_STOCK_QUOTES",
                    "可购买报价快照数最多",
                    f"按 {day_7} 的 in_stock_quote_count 降序比较商品。",
                    view=view,
                    fields=("product_id", "in_stock_quote_count"),
                ),
            ],
            interpretation="报价快照质量指标存在歧义",
        )

    if re.fullmatch(r"工具表现(?:怎么样|如何)?[。！？!?]?", normalized):
        view = "analytics_tool_quality_daily"
        return DataAnalysisPlan(
            status="NEEDS_CLARIFICATION",
            clarification_question="“工具表现”希望按哪项技术调用指标查看？",
            clarification_options=[
                _catalog_clarification_option(
                    "LAST_7D_COUNTS",
                    "最近 7 天调用状态计数",
                    f"汇总 {day_7} 各工具的 call_count、success_count 和 failure_count。",
                    view=view,
                    fields=("tool_name", "call_count", "success_count", "failure_count"),
                ),
                _catalog_clarification_option(
                    "LAST_7D_FAILURE_RATE",
                    "最近 7 天失败调用比率",
                    f"按 {day_7} 的 SUM(failure_count)/SUM(call_count) 比较工具。",
                    view=view,
                    fields=("tool_name", "failure_count", "call_count"),
                ),
                _catalog_clarification_option(
                    "LAST_7D_LATENCY",
                    "最近 7 天加权平均延迟",
                    f"按 {day_7} 以 call_count 加权的 avg_latency_ms 比较工具。",
                    view=view,
                    fields=("tool_name", "call_count", "avg_latency_ms"),
                ),
            ],
            interpretation="工具技术质量指标存在歧义",
        )

    product_match = re.fullmatch(
        r"商品\s*([A-Za-z0-9_-]+)\s*最近表现(?:怎么样|如何)?[。！？!?]?",
        normalized,
    )
    if product_match:
        product_id = product_match.group(1)
        return DataAnalysisPlan(
            status="NEEDS_CLARIFICATION",
            clarification_question=f"“{product_id} 最近表现”希望看哪段期间和哪类表现？",
            clarification_options=[
                _catalog_clarification_option(
                    "LAST_7D_PRODUCT_SALES",
                    "最近 7 天商品销售",
                    f"汇总商品 {product_id} 在 {day_7} 的 paid_units、gross_item_amount 和 refunded_units。",
                    view="analytics_product_sales_daily",
                    fields=("product_id", "paid_units", "gross_item_amount", "refunded_units"),
                ),
                _catalog_clarification_option(
                    "LAST_7D_RECOMMENDATION_EVENTS",
                    "最近 7 天推荐结果事件",
                    f"汇总商品 {product_id} 在 {day_7} 的 VERIFIED 推荐支付、退款、退货、低分评价、售后联系和复购事件。",
                    view="analytics_recommendation_quality_daily",
                    fields=(
                        "product_id",
                        "payment_count",
                        "refund_count",
                        "return_count",
                        "negative_review_count",
                        "support_contact_count",
                        "repeat_purchase_count",
                    ),
                ),
                _catalog_clarification_option(
                    "LAST_7D_OFFER_QUALITY",
                    "最近 7 天报价快照",
                    f"列出商品 {product_id} 在 {day_7} 的报价快照数量、平均基础价和平均估算到手价。",
                    view="analytics_offer_quality_daily",
                    fields=("product_id", "quote_count", "avg_base_price", "avg_estimated_payable"),
                ),
            ],
            interpretation="商品表现的指标域存在歧义",
        )
    return None


def _question_dates(question: str) -> tuple[date, date]:
    match = re.search(r"最近\s*(\d+)\s*天", question)
    days = min(90, max(1, int(match.group(1) if match else 7)))
    fixed_now = str(getattr(get_settings(), "analytics_eval_fixed_now", "") or "").strip()
    end = (
        datetime.fromisoformat(fixed_now).date()
        if fixed_now
        else datetime.now(ZoneInfo(CATALOG_TIMEZONE)).date()
    )
    return end - timedelta(days=days - 1), end


def _request_data_as_of() -> str:
    settings = get_settings()
    fixed_now = str(getattr(settings, "analytics_eval_fixed_now", "") or "").strip()
    if fixed_now:
        value = datetime.fromisoformat(fixed_now)
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo(CATALOG_TIMEZONE))
    else:
        value = datetime.now(ZoneInfo(CATALOG_TIMEZONE))
    return value.isoformat(timespec="seconds")


def _capability_boundary() -> str:
    return (
        f"catalog/capability boundary：仅覆盖 {CATALOG_VERSION} 的十个受治理单视图；"
        "V0 不支持跨视图 Join、窗口函数、同比环比或正式财务口径。"
    )


def analytics_no_query_contract(*required_facts: str) -> dict[str, Any]:
    boundary = _capability_boundary()
    facts = list(
        dict.fromkeys(
            [
                *(str(item).strip() for item in required_facts if str(item).strip()),
                "no SQL",
                "catalog/capability boundary",
                "dataAsOf",
                "catalogVersion",
            ]
        )
    )
    return {
        "catalogVersion": CATALOG_VERSION,
        "catalogContentSha256": CATALOG_CONTENT_SHA256,
        "dataAsOf": _request_data_as_of(),
        "dataAsOfScope": "REQUEST_EVALUATED_AT_NO_QUERY",
        "capabilityBoundary": boundary,
        "queryExecuted": False,
        "requiredFacts": facts,
        "answerBoundary": {
            "mustDisclose": facts,
            "forbiddenClaims": [],
        },
        "provisional": True,
    }


def _response_contract_errors(result: dict[str, Any]) -> list[str]:
    outcome = result.get("outcome")
    if outcome not in {"ANSWER", "CLARIFY", "ABSTAIN", "DENY"}:
        return []
    errors: list[str] = []
    if result.get("catalogVersion") != CATALOG_VERSION:
        errors.append("CATALOG_VERSION_MISSING")
    if not str(result.get("dataAsOf") or "").strip():
        errors.append("DATA_AS_OF_MISSING")
    if not str(result.get("answer") or "").strip():
        errors.append("ANSWER_MISSING")
    completion = str(result.get("completion") or "")
    expected = {
        "ANSWER": {"COMPLETE", "PARTIAL"},
        "CLARIFY": {"NOT_APPLICABLE"},
        "ABSTAIN": {"NOT_APPLICABLE"},
        "DENY": {"NOT_APPLICABLE"},
    }
    if completion not in expected[outcome]:
        errors.append("COMPLETION_INVALID")
    if outcome == "ANSWER":
        if "统计期间：" not in str(result.get("answer") or ""):
            errors.append("PERIOD_MISSING")
        boundary = result.get("answerBoundary") or {}
        text = "\n".join(
            [
                str(result.get("answer") or ""),
                *(str(item) for item in result.get("highlights") or []),
            ]
        )
        for claim in boundary.get("forbiddenClaims") or []:
            if str(claim) and str(claim) in text:
                errors.append(f"FORBIDDEN_CLAIM:{claim}")
    else:
        if result.get("sql") or result.get("queries") or result.get("queryExecuted") is not False:
            errors.append("NO_QUERY_CONTRACT_INVALID")
        if outcome in {"ABSTAIN", "DENY"} and not result.get("reasonCode"):
            errors.append("REASON_CODE_MISSING")
        if outcome == "ABSTAIN" and "catalog/capability boundary" not in str(
            result.get("capabilityBoundary") or ""
        ):
            errors.append("CAPABILITY_BOUNDARY_MISSING")
        if outcome == "CLARIFY":
            options = result.get("clarificationOptions") or []
            choice_ids = [str(item.get("choiceId") or "") for item in options]
            if len(options) < 2 or len(choice_ids) != len(set(choice_ids)):
                errors.append("CLARIFICATION_OPTIONS_INVALID")
            if not result.get("clarificationToken"):
                errors.append("CLARIFICATION_TOKEN_MISSING")
            if int(result.get("clarificationTokenTtlSeconds") or 0) != 900:
                errors.append("CLARIFICATION_TTL_INVALID")
    return errors


def _contract_failure(result: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    return {
        "runId": result.get("runId"),
        "outcome": None,
        "completion": "FAILED",
        "status": "ANALYTICS_RESPONSE_CONTRACT_FAILED",
        "catalogVersion": CATALOG_VERSION,
        "dataAsOf": result.get("dataAsOf") or _request_data_as_of(),
        "warnings": ["ANALYTICS_RESPONSE_CONTRACT_FAILED", *errors],
    }


def _metric_definitions(plan: DataAnalysisPlan) -> list[dict[str, str]]:
    if not plan.semantic_view or plan.semantic_view not in CATALOG:
        return []
    item = CATALOG[plan.semantic_view]
    definitions = dict(item["columns"])
    definitions.update(
        {name: spec.get("definition") for name, spec in (item.get("derived_metrics") or {}).items()}
    )
    selected = [*plan.dimensions, *plan.metrics]
    return [
        {"name": name, "definition": str(definitions[name])}
        for name in selected
        if name in definitions
    ]


def _metric_tree_definitions(plan: DataAnalysisPlan) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for branch in plan.branches:
        item = CATALOG.get(branch.semantic_view, {})
        columns = dict(item.get("columns") or {})
        columns.update(
            {
                name: spec.get("definition")
                for name, spec in (item.get("derived_metrics") or {}).items()
            }
        )
        for name in [*branch.dimensions, *branch.metrics]:
            key = (branch.semantic_view, name)
            if name not in columns or key in seen:
                continue
            seen.add(key)
            output.append(
                {
                    "name": name,
                    "definition": str(columns[name]),
                    "semanticView": branch.semantic_view,
                    "branchId": branch.branch_id,
                }
            )
    return output


def _structured_json_llm(schema: type[BaseModel]):
    # DeepSeek V4 enables thinking by default. Its thinking mode rejects forced
    # tool calls, while its current API also rejects json_schema response format.
    # DataAnalyst is bounded extraction work, so use non-thinking json_object mode.
    llm = create_memory_llm(disable_thinking=True)
    return llm.with_structured_output(schema, method="json_mode", include_raw=True)


def _schema_instruction(schema: type[BaseModel]) -> str:
    return json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":"))


async def _explain_sql(sql: str, timeout_ms: int, cursor: Any | None = None) -> list[dict]:
    if cursor is not None:
        await cursor.execute("SET SESSION MAX_EXECUTION_TIME=%s", (timeout_ms,))
        await cursor.execute("EXPLAIN " + sql)
        return list(await cursor.fetchall())
    async with acquire_analytics() as cursor:
        await cursor.execute("SET SESSION MAX_EXECUTION_TIME=%s", (timeout_ms,))
        await cursor.execute("EXPLAIN " + sql)
        return list(await cursor.fetchall())


async def _execute_sql(sql: str, timeout_ms: int, cursor: Any | None = None) -> list[dict]:
    if cursor is not None:
        await cursor.execute("SET SESSION MAX_EXECUTION_TIME=%s", (timeout_ms,))
        await cursor.execute(sql)
        return list(await cursor.fetchall())
    async with acquire_analytics() as cursor:
        await cursor.execute("SET SESSION MAX_EXECUTION_TIME=%s", (timeout_ms,))
        await cursor.execute(sql)
        return list(await cursor.fetchall())


def _database_error_code(exc: Exception) -> int | None:
    args = getattr(exc, "args", ())
    return int(args[0]) if args and isinstance(args[0], int) else None


def _explain_scan_estimate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    estimates: list[int] = []
    tables: list[dict[str, Any]] = []
    for row in rows:
        raw_estimate = (
            row.get("rows") if row.get("rows") is not None else row.get("rows_examined_per_scan")
        )
        try:
            estimated = max(0, int(raw_estimate or 0))
        except (TypeError, ValueError):
            estimated = 0
        estimates.append(estimated)
        tables.append(
            {
                "table": row.get("table") or row.get("table_name"),
                "accessType": row.get("type") or row.get("access_type"),
                "estimatedRows": estimated,
                "filteredPercent": row.get("filtered"),
            }
        )
    return {
        "estimatedRows": sum(estimates),
        "tables": tables,
        "diagnosticOnly": True,
        "hardThresholdApplied": False,
    }


def _json_size(value: object) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def _page_result_rows(
    rows: list[dict],
    *,
    offset: int,
    page_size: int,
    max_bytes: int,
) -> tuple[list[dict], bool, bool]:
    """Return rows bounded by both page size and serialized byte budget."""
    page: list[dict] = []
    byte_limited = False
    for row in rows[offset : offset + page_size]:
        candidate = [*page, row]
        if _json_size(candidate) > max_bytes:
            if not page:
                return [], False, True
            byte_limited = True
            break
        page.append(row)
    return page, byte_limited, False


_METRIC_LABELS = {
    "gross_paid_amount": "已支付金额",
    "completed_refund_amount": "已完成退款金额",
    "net_paid_amount": "净支付金额",
    "paid_order_count": "已支付订单数",
    "stockout_sku_count": "缺货 SKU 数量",
    "stock": "当前库存",
    "current_stock": "当前库存",
    "suggested_replenish_quantity": "建议补货量",
    "run_count": "Agent 运行量",
    "success_count": "成功量",
    "failure_count": "失败量",
    "avg_latency_ms": "平均延迟",
}


def _display_metric(plan: DataAnalysisPlan, name: str) -> str:
    if name in _METRIC_LABELS:
        return _METRIC_LABELS[name]
    columns = CATALOG.get(str(plan.semantic_view or ""), {}).get("columns") or {}
    definition = str(columns.get(name) or "").strip()
    return definition or name


def _format_metric_value(plan: DataAnalysisPlan, metric: str, value: Number) -> str:
    normalized, column_types = normalize_typed_rows(
        [{metric: value}],
        view=str(plan.semantic_view or ""),
        columns=[metric],
    )
    display = str(normalized[0][metric])
    unit = str(column_types[metric].get("unit") or "").strip()
    return f"{display} {unit}" if unit else display


def _deterministic_narrative(
    plan: DataAnalysisPlan,
    rows: list[dict],
) -> DataNarrative:
    """Derive numeric claims from returned rows so model prose cannot misread data."""
    if not rows:
        return DataNarrative(answer="当前时间范围内没有可用数据。")
    dimension = next(
        (
            name
            for name in [*plan.dimensions, "date", "snapshot_date"]
            if any(row.get(name) not in (None, "") for row in rows)
        ),
        None,
    )
    metric_names = [
        name
        for name in plan.metrics
        if any(
            isinstance(row.get(name), Number) and not isinstance(row.get(name), bool)
            for row in rows
        )
    ]
    if not metric_names:
        metric_names = [
            name
            for name in rows[0]
            if name != dimension
            and any(
                isinstance(row.get(name), Number) and not isinstance(row.get(name), bool)
                for row in rows
            )
        ][:8]

    statements: list[str] = []
    highlights: list[str] = []
    for metric in metric_names[:8]:
        points = [
            (row.get(dimension) if dimension else None, row.get(metric))
            for row in rows
            if isinstance(row.get(metric), Number) and not isinstance(row.get(metric), bool)
        ]
        if not points:
            continue
        maximum = max(points, key=lambda item: Decimal(str(item[1])))
        minimum = min(points, key=lambda item: Decimal(str(item[1])))
        label = _display_metric(plan, metric)
        dimension_suffix = f"（{maximum[0]}）" if maximum[0] not in (None, "") else ""
        if Decimal(str(maximum[1])) == Decimal(str(minimum[1])):
            statement = (
                f"{label}在返回结果中均为{_format_metric_value(plan, metric, maximum[1])}"
            )
        else:
            minimum_suffix = f"（{minimum[0]}）" if minimum[0] not in (None, "") else ""
            statement = (
                f"{label}最大值为{_format_metric_value(plan, metric, maximum[1])}"
                f"{dimension_suffix}，最小值为"
                f"{_format_metric_value(plan, metric, minimum[1])}{minimum_suffix}"
            )
        statements.append(statement)
        highlights.append(
            f"{label}最大值：{_format_metric_value(plan, metric, maximum[1])}"
            f"{dimension_suffix}"
        )

        if dimension in {"date", "snapshot_date"} and len(points) > 1:
            ordered = sorted(points, key=lambda item: str(item[0] or ""))
            first, last = ordered[0], ordered[-1]
            statements.append(
                f"{label}从{first[0]}的{_format_metric_value(plan, metric, first[1])}"
                f"变为{last[0]}的{_format_metric_value(plan, metric, last[1])}"
            )

    if dimension in {"date", "snapshot_date"} and plan.start_date and plan.end_date:
        expected_days = (plan.end_date - plan.start_date).days + 1
        observed_days = len(
            {str(row.get(dimension)) for row in rows if row.get(dimension) not in (None, "")}
        )
        coverage = (
            f"计划范围为{expected_days}天，返回{observed_days}个有数据日期；"
            "未返回日期不能直接视为指标为0"
        )
        statements.append(coverage)
        highlights.append(coverage)

    if not statements:
        statements.append(f"已返回{len(rows)}条受治理聚合结果，未发现可计算的数值指标")
    return DataNarrative(
        answer="；".join(statements) + "。",
        highlights=highlights[:5],
    )


def _supply_chain_disclosures(plan: DataAnalysisPlan) -> tuple[list[str], list[str]]:
    branches = [
        branch
        for branch in plan.branches
        if branch.semantic_view in SUPPLY_CHAIN_COMPILER_VIEWS
    ]
    if not branches:
        return [], []
    facts = ["current snapshot only"]
    statements = ["库存结果仅代表当前快照"]
    if len({branch.semantic_view for branch in branches}) > 1:
        facts.extend(["two separate result tables", "no cross-view join"])
        statements.append("结果按两个视图分别展示，未合并数据")

    for branch in branches:
        metrics = set(branch.metrics)
        dimensions = set(branch.dimensions)
        filters = {item.column: item for item in branch.filters}
        orders = {item.column: item.direction for item in branch.order_by}
        if branch.semantic_view == "analytics_inventory_risk":
            stock_filter = filters.get("stock")
            if (
                stock_filter is not None
                and stock_filter.operator == "LTE"
                and stock_filter.value == 0
            ) or ("stockout_sku_count" in metrics and "risk_level" not in dimensions):
                facts.append("stock<=0 definition")
                statements.append("缺货定义为 stock<=0")
            if "property_value_id_hash" in dimensions:
                facts.append("SKU hash")
            if stock_filter is not None and stock_filter.operator == "BETWEEN":
                facts.extend(
                    [
                        "low stock definition: 1..10",
                        "exclude stock<=0",
                        "stable ascending order",
                    ]
                )
                statements.append("低库存定义为 1<=stock<=10，排除 stock<=0，并按库存稳定升序")
            if branch.top_k < 200 and orders.get("stock") == "DESC":
                facts.extend(
                    [
                        f"top {branch.top_k}",
                        "tie-break: product_id then SKU hash",
                    ]
                )
                statements.append(
                    f"返回前 {branch.top_k} 项，并列时依次按 product_id、SKU 哈希排序"
                )
            continue

        facts.extend(
            [
                "human planning input",
                "data coverage boundary",
                "not a stockout probability",
            ]
        )
        statements.append("库存预测属于人工规划输入，受数据覆盖边界约束，不表示缺货概率")
        if "suggested_replenish_quantity" in metrics:
            facts.extend(["human replenishment suggestion", "not a purchase instruction"])
            statements.append("suggested_replenish_quantity 是人工补货建议，不是采购指令")
        if "ewma_daily_demand" in metrics:
            facts.append("28-day EWMA demand input")
        if {"lead_time_days", "safety_stock"}.issubset(metrics):
            facts.append("lead time and safety stock")
        if {"min_order_quantity", "review_period_days"}.issubset(metrics):
            facts.append("MOQ and review period")
        if "confidence" in metrics:
            facts.extend(["confidence means data coverage", "not statistical confidence"])
            statements.append("confidence 表示有效销售日的数据覆盖度，不是统计置信度")
        coverage_filter = filters.get("coverage_days")
        if coverage_filter is not None and coverage_filter.operator == "LT":
            facts.append("exclude NULL coverage")
            statements.append("coverage_days 不可计算的记录已排除")
        if branch.top_k < 200 and orders.get("suggested_replenish_quantity") == "DESC":
            facts.extend(
                [
                    f"top {branch.top_k}",
                    "tie-break: product_id then sku_key",
                ]
            )
            statements.append(
                f"返回前 {branch.top_k} 项，并列时依次按 product_id、sku_key 排序"
            )
    return list(dict.fromkeys(facts)), list(dict.fromkeys(statements))


def _quality_disclosures(
    plan: DataAnalysisPlan,
    *,
    result: dict[str, Any] | None = None,
) -> tuple[list[str], list[str]]:
    """Render the narrow, view-specific boundaries for quality event counts."""
    agent_view = "analytics_agent_quality_daily"
    branches = [
        branch
        for branch in plan.branches
        if branch.semantic_view in {*QUALITY_COMPILER_VIEWS, agent_view}
    ]
    if not branches:
        return [], []

    facts: list[str] = []
    statements: list[str] = []
    if any(branch.semantic_view == agent_view for branch in branches):
        facts.append("technical status only")
    snapshots = {
        str(item.get("branchId")): item
        for item in (result or {}).get("branches") or []
        if isinstance(item, dict)
    }
    for branch in branches:
        metrics = set(branch.metrics)
        if branch.semantic_view == "analytics_recommendation_quality_daily":
            facts.extend(["event-day attribution", "VERIFIED event-day attribution"])
            statements.append("推荐结果只按 VERIFIED 事件发生日统计")
            if metrics == {
                "payment_count",
                "refund_count",
                "return_count",
                "negative_review_count",
                "support_contact_count",
                "repeat_purchase_count",
            }:
                facts.append("all VERIFIED result event counts")
            if "refund_count" in metrics:
                facts.append("VERIFIED refund events")
            if "return_count" in metrics:
                facts.append("VERIFIED return events")
            if "payment_count" in metrics:
                facts.append("VERIFIED payment events")
            if "repeat_purchase_count" in metrics:
                facts.append("VERIFIED repeat-purchase events")
            filter_columns = {item.column for item in branch.filters}
            if filter_columns == {"negative_review_count", "support_contact_count"}:
                facts.append("OR filter")
                statements.append("按低分评价或售后联系任一事件筛选商品")
            if "导出" in branch.purpose:
                facts.append("export requires ANALYTICS_EXPORT")
                statements.append("导出需要 ANALYTICS_EXPORT 权限，并沿用冻结结果")
            continue

        facts.append("technical call status")
        statements.append("工具质量只反映技术调用状态和延迟，不代表业务层判断")
        orders = {item.column: item.direction for item in branch.order_by}
        if (
            branch.top_k == 1
            and "failure_count" in metrics
            and orders.get("failure_count") == "DESC"
        ):
            facts.extend(["top 1", "stable tie-break"])
            statements.append("失败调用数取前 1 项，并列按工具名稳定排序")

        snapshot = snapshots.get(branch.branch_id) or {}
        if branch.semantic_view == agent_view:
            continue
        if snapshot.get("status") in {"SUCCEEDED", "EMPTY_RESULT"}:
            facts.append("tool branch succeeds")

    status_by_branch = {
        branch_id: str(snapshot.get("status") or "")
        for branch_id, snapshot in snapshots.items()
    }
    agent_status = next(
        (
            status_by_branch.get(branch.branch_id, "")
            for branch in branches
            if branch.semantic_view == agent_view
        ),
        "",
    )
    failed_quality_branch = any(
        status_by_branch.get(branch.branch_id, "")
        not in {"", "SUCCEEDED", "EMPTY_RESULT"}
        for branch in branches
    )
    tool_branch_succeeded = any(
        branch.semantic_view != agent_view
        and status_by_branch.get(branch.branch_id) in {"SUCCEEDED", "EMPTY_RESULT"}
        for branch in branches
    )
    if agent_status == "QUERY_TIMEOUT":
        facts.extend(["agent branch timeout", "partial completion"])
        statements.append(
            "Agent 分支超时，"
            + (
                "已返回成功的工具分支并标记部分完成"
                if tool_branch_succeeded
                else "结果标记为部分完成"
            )
        )
    elif agent_status and agent_status not in {"SUCCEEDED", "EMPTY_RESULT"}:
        facts.extend(["agent branch failure", "partial completion"])
        statements.append(
            "Agent 分支未完成，"
            + (
                "已返回成功的工具分支并标记部分完成"
                if tool_branch_succeeded
                else "结果标记为部分完成"
            )
        )
    elif failed_quality_branch:
        facts.append("partial completion")
        statements.append("部分分析分支未完成，结果已标记为部分完成")
    elif (result or {}).get("completion") == "PARTIAL":
        facts.append("partial completion")
        statements.append("结果已标记为部分完成；未将原因推断为超时")
    return list(dict.fromkeys(facts)), list(dict.fromkeys(statements))


class DataAnalystService:
    @staticmethod
    def _validate_plan_dates_and_contract(
        plan: DataAnalysisPlan,
        *,
        default_start: date,
        default_end: date,
    ) -> DataAnalysisPlan:
        """Apply deterministic limits after parsing the model's plan."""
        max_days = get_settings().analytics_max_days
        if not plan.branches or len(plan.branches) > 3:
            raise ValueError("DATA_ANALYST_BRANCH_COUNT_INVALID")
        branch_ids: set[str] = set()
        for index, branch in enumerate(plan.branches, start=1):
            if branch.branch_id in branch_ids:
                raise ValueError("DATA_ANALYST_BRANCH_DUPLICATE")
            branch_ids.add(branch.branch_id)
            if branch.semantic_view not in CATALOG:
                raise ValueError("DATA_ANALYST_VIEW_INVALID")
            date_column = str(CATALOG[branch.semantic_view].get("date_column") or "")
            if date_column and date_column != "date":
                branch.dimensions = [
                    date_column if dimension == "date" else dimension
                    for dimension in branch.dimensions
                ]
            columns = allowed_plan_fields(branch.semantic_view)
            selected = [*branch.metrics, *branch.dimensions]
            condition_columns = [
                *(item.column for item in branch.filters),
                *(item.column for item in branch.order_by),
            ]
            if (
                not branch.metrics
                or not set([*selected, *condition_columns]).issubset(columns)
                or branch.top_k > get_settings().analytics_max_rows
            ):
                raise ValueError("DATA_ANALYST_COLUMN_INVALID")
            if str(CATALOG[branch.semantic_view].get("answerability") or "") in {
                "CURRENT_SNAPSHOT_ONLY",
                "PLANNING_INPUT_ONLY",
            }:
                branch.start_date = default_end
                branch.end_date = default_end
            else:
                branch.start_date = branch.start_date or default_start
                branch.end_date = branch.end_date or default_end
            if (
                branch.end_date < branch.start_date
                or (branch.end_date - branch.start_date).days + 1 > max_days
            ):
                raise ValueError("DATA_ANALYST_DATE_RANGE_INVALID")
            if not branch.branch_id:
                branch.branch_id = f"metric-{index}"
        first = plan.branches[0]
        plan.semantic_view = first.semantic_view
        plan.metrics = list(first.metrics)
        plan.dimensions = list(first.dimensions)
        plan.start_date = first.start_date
        plan.end_date = first.end_date
        return plan

    async def _plan(self, question: str) -> DataAnalysisPlan:
        start, end = _question_dates(question)
        clarification = _catalog_clarification(question, end)
        if clarification is not None:
            return clarification
        messages = [
            SystemMessage(
                content=(
                    "你是电商经营分析规划 Agent。只选择下方语义视图、字段和受治理派生指标，"
                    "不得假设原始表。问题有业务口径歧义时返回 NEEDS_CLARIFICATION。"
                    "NEEDS_CLARIFICATION 必须给出 clarification_question 及至少两个唯一 choice_id 的"
                    " clarification_options；每项必须含 label 和可直接追加到原问题的 answer_suffix。"
                    "复杂问题拆成最多三个相互独立的指标树分支；每个分支只能选择一个语义视图，"
                    "使用唯一 branch_id，并写清该分支验证什么。每个 READY 分支的 metrics 至少一项；"
                    "派生指标必须使用目录中的指标名。所有非日期条件必须写入 filters，排序写入"
                    " order_by，前 N 写入 top_k；不能只把条件写在 purpose。日期只使用 start_date/"
                    "end_date，不放入 filters。SKU 明细必须包含目录 grain 中的 SKU 键；缺货明细"
                    "metrics 使用 stock，只有缺货总数才使用 stockout_sku_count。简单问题只生成一个分支。"
                    "严格只返回一个 JSON 对象，不得输出 Markdown。"
                    f"JSON Schema：{_schema_instruction(DataAnalysisPlan)}"
                )
            ),
            HumanMessage(
                content=(
                    f"今天={end.isoformat()}；默认时间范围="
                    f"{start.isoformat()} 到 {end.isoformat()}。\n"
                    f"语义目录：\n{catalog_prompt()}\n管理员问题：{question}"
                )
            ),
        ]
        plan: DataAnalysisPlan | None = None
        for attempt in range(2):
            structured = _structured_json_llm(DataAnalysisPlan)
            try:
                response = await asyncio.wait_for(
                    invoke_llm_with_metrics(structured, messages),
                    timeout=get_settings().analytics_model_timeout_seconds,
                )
            except TimeoutError as exc:
                if attempt == 1:
                    raise ValueError("DATA_ANALYST_PLAN_TIMEOUT") from exc
                continue
            parsed = response.get("parsed") if isinstance(response, dict) else response
            parsing_error = response.get("parsing_error") if isinstance(response, dict) else None
            if parsing_error is None:
                try:
                    plan = DataAnalysisPlan.model_validate(parsed)
                    break
                except ValueError:
                    pass
            if attempt == 0:
                messages.append(
                    SystemMessage(
                        content=(
                            "上一版计划未通过结构校验。重新生成完整 JSON；READY 状态下每个分支"
                            "必须包含至少一个目录允许的 metrics，并显式给出 filters、order_by、top_k。"
                            "缺货总数使用 stockout_sku_count；缺货明细使用 stock 并包含 SKU grain。"
                        )
                    )
                )
        if plan is None:
            raise ValueError("DATA_ANALYST_PLAN_PARSE_FAILED")
        plan = _normalize_supply_chain_plan(question, plan, end=end)
        if plan.status == "NEEDS_CLARIFICATION":
            return plan
        return self._validate_plan_dates_and_contract(
            plan,
            default_start=start,
            default_end=end,
        )

    async def _draft_sql(
        self,
        question: str,
        plan: DataAnalysisPlan,
        *,
        branch: DataAnalysisBranch | None = None,
        feedback: str | None = None,
    ) -> str:
        branch = branch or plan.branches[0]
        view = str(branch.semantic_view)
        structured = _structured_json_llm(SqlDraft)
        try:
            response = await asyncio.wait_for(
                invoke_llm_with_metrics(
                    structured,
                    [
                        SystemMessage(
                            content=(
                                "你是受治理的 MySQL 8 Text2SQL Agent。只输出 SqlDraft。"
                                "只允许单条 SELECT、一个指定语义视图、显式列名和 LIMIT<=200；"
                                "计划选择受治理派生指标时，必须逐字使用 viewContract 中的"
                                " sql_expression 并以该指标名作为别名；"
                                "时间序列必须按计划日期使用 date BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD'；"
                                "禁止 SELECT *、JOIN、子查询、OR、OFFSET、跨库、系统表和任何写操作。"
                                "严格只返回一个 JSON 对象，不得输出 Markdown。"
                                f"JSON Schema：{_schema_instruction(SqlDraft)}"
                            )
                        ),
                        HumanMessage(
                            content=json.dumps(
                                {
                                    "question": question,
                                    "plan": {
                                        "interpretation": plan.interpretation,
                                        "branch": branch.model_dump(mode="json"),
                                    },
                                    "viewContract": CATALOG[view],
                                    "previousFailureCode": feedback,
                                },
                                ensure_ascii=False,
                            )
                        ),
                    ],
                ),
                timeout=get_settings().analytics_model_timeout_seconds,
            )
        except TimeoutError as exc:
            raise ValueError("DATA_ANALYST_SQL_TIMEOUT") from exc
        parsed = response.get("parsed") if isinstance(response, dict) else response
        parsing_error = response.get("parsing_error") if isinstance(response, dict) else None
        if parsing_error is not None:
            raise ValueError("DATA_ANALYST_SQL_PARSE_FAILED")
        return SqlDraft.model_validate(parsed).sql.strip()

    async def _narrative(
        self,
        question: str,
        plan: DataAnalysisPlan,
        rows: list[dict],
    ) -> DataNarrative:
        _ = question
        return _deterministic_narrative(plan, rows)

    @staticmethod
    def _chart(columns: list[str], rows: list[dict]) -> dict | None:
        if not rows or not columns:
            return None
        x = next(
            (
                name
                for name in ("date", "snapshot_date", "product_name", "agent_id", "tool_name")
                if name in columns
            ),
            columns[0],
        )
        numeric = [
            name
            for name in columns
            if name != x
            and any(
                isinstance(row.get(name), Number) and not isinstance(row.get(name), bool)
                for row in rows
            )
        ][:4]
        if not numeric:
            return None
        return {
            "type": "line" if x in {"date", "snapshot_date"} else "bar",
            "x": x,
            "series": numeric,
        }

    @staticmethod
    def _branch_plan(plan: DataAnalysisPlan, branch: DataAnalysisBranch) -> DataAnalysisPlan:
        return DataAnalysisPlan(
            semantic_view=branch.semantic_view,
            metrics=list(branch.metrics),
            dimensions=list(branch.dimensions),
            start_date=branch.start_date,
            end_date=branch.end_date,
            interpretation=branch.purpose or plan.interpretation,
        )

    async def _finalize_answer(
        self,
        result: dict[str, Any],
        plan: DataAnalysisPlan,
        *,
        admin_id: str,
        permissions: Iterable[str],
        tenant_id: str | None,
        data_as_of: str,
        page_size: int,
    ) -> dict[str, Any]:
        settings = get_settings()
        full_rows = list(result.get("rows") or [])[: settings.analytics_max_rows]
        columns = list(result.get("columns") or [])[:20]
        column_types = dict(result.get("columnTypes") or {})
        page_rows, byte_limited, row_too_large = _page_result_rows(
            full_rows,
            offset=0,
            page_size=page_size,
            max_bytes=int(settings.analytics_max_result_bytes),
        )
        if row_too_large:
            return {
                "runId": result.get("runId"),
                "outcome": None,
                "completion": "FAILED",
                "status": "RESULT_TOO_LARGE",
                "warnings": ["单行结果超过 analytics_max_result_bytes"],
            }
        warnings = list(result.get("warnings") or [])
        if byte_limited:
            warnings.append("RESULT_BYTES_TRUNCATED")
        if len(full_rows) >= settings.analytics_max_rows and not byte_limited:
            warnings.append("ANALYTICS_MAX_ROWS_REACHED")
        views = [branch.semantic_view for branch in plan.branches]
        disclosure = disclosure_contract(views)
        supply_facts, supply_disclosures = _supply_chain_disclosures(plan)
        quality_facts, quality_disclosures = _quality_disclosures(plan, result=result)
        semantic_facts = list(dict.fromkeys([*supply_facts, *quality_facts]))
        semantic_disclosures = list(dict.fromkeys([*supply_disclosures, *quality_disclosures]))
        periods = list(
            dict.fromkeys(
                f"{branch.start_date.isoformat()} 至 {branch.end_date.isoformat()}"
                for branch in plan.branches
                if branch.start_date and branch.end_date
            )
        )
        source_text = (
            f"统计期间：{'；'.join(periods) or '当前快照'}；dataAsOf={data_as_of}；"
            f"口径来源={CATALOG_VERSION}。"
        )
        units = list(
            dict.fromkeys(
                f"{column}={str(contract.get('unit')).upper()}"
                for column, contract in column_types.items()
                if str(contract.get("unit") or "").strip()
            )
        )
        if units:
            source_text += f"单位：{'；'.join(units)}。"
        has_money = any(
            str(contract.get("unit") or "").upper() == "CNY" for contract in column_types.values()
        )
        if has_money:
            source_text += "金额为暂定口径，仅供运营核对，不作为结算或审计结论。"
        boundary_text = (
            f"口径边界：{'；'.join(disclosure['mustDisclose'])}。"
            if disclosure["mustDisclose"]
            else ""
        )
        semantic_text = (
            f"分析口径：{'；'.join(semantic_disclosures)}。"
            if semantic_disclosures
            else ""
        )
        answer = str(result.get("answer") or "").strip()
        for line in (source_text, boundary_text, semantic_text):
            if line and line not in answer:
                answer = f"{answer}\n{line}".strip()
        branch_snapshots = list(result.get("branches") or [])
        if not branch_snapshots:
            branch_snapshots = [
                {
                    "branchId": plan.branches[0].branch_id,
                    "status": result.get("status"),
                    "columns": columns,
                    "columnTypes": column_types,
                    "rows": full_rows,
                    "sql": result.get("sql"),
                    "lineage": result.get("lineage") or [],
                    "explain": result.get("explain") or [],
                    "explainDiagnostic": result.get("explainDiagnostic"),
                    "scanEstimate": result.get("scanEstimate"),
                }
            ]
        snapshot = await analytics_result_service.freeze(
            admin_id=admin_id,
            permissions=permissions,
            tenant_id=tenant_id,
            data_as_of=data_as_of,
            columns=columns,
            column_types=column_types,
            rows=full_rows,
            branches=branch_snapshots,
            lineage=list(result.get("lineage") or []),
            queries=list(result.get("queries") or []),
        )
        next_cursor = None
        if snapshot is None:
            warnings.append("RESULT_SNAPSHOT_UNAVAILABLE")
        elif len(page_rows) < len(full_rows):
            next_cursor = analytics_result_service.cursor(snapshot, len(page_rows))
        failed_branches = [
            branch
            for branch in branch_snapshots
            if branch.get("status") not in {"SUCCEEDED", "EMPTY_RESULT"}
        ]
        completion = "PARTIAL" if failed_branches else "COMPLETE"
        response = {
            **result,
            "outcome": "ANSWER",
            "completion": completion,
            "answer": answer,
            "columns": columns,
            "columnTypes": column_types,
            "rows": page_rows,
            "catalogVersion": CATALOG_VERSION,
            "catalogContentSha256": CATALOG_CONTENT_SHA256,
            "dataAsOf": data_as_of,
            "provisional": True,
            "answerBoundary": disclosure,
            "requiredFacts": list(
                dict.fromkeys([*(result.get("requiredFacts") or []), *semantic_facts])
            ),
            "warnings": list(dict.fromkeys(warnings)),
            "nextCursor": next_cursor,
            "page": {
                "offset": 0,
                "size": len(page_rows),
                "hasMore": bool(next_cursor),
                "totalRows": len(full_rows),
                "maxRows": settings.analytics_max_rows,
            },
        }
        if snapshot is not None:
            response.update(
                {
                    "resultSetId": snapshot["resultSetId"],
                    "resultSnapshotExpiresAt": snapshot["resultSnapshotExpiresAt"],
                    "resultHash": snapshot["resultHash"],
                }
            )
        return response

    async def _execute_metric_branch(
        self,
        question: str,
        plan: DataAnalysisPlan,
        branch: DataAnalysisBranch,
        *,
        run_id: str,
        cursor: Any | None = None,
        access_policy: AnalyticsAccessPolicy | None = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        branch_plan = self._branch_plan(plan, branch)
        sql = ""
        guard: SqlGuardResult | None = None
        explain: list[dict] = []
        explain_diagnostic: dict[str, Any] | None = None
        scan_estimate: dict[str, Any] | None = None
        warnings: list[str] = []
        feedback: str | None = None
        compiled_sql = _compile_branch_sql(branch)
        sql_source = "DETERMINISTIC_COMPILER" if compiled_sql is not None else "LLM_SQL"
        if compiled_sql is not None:
            episode_service.record_step(
                "DATA_ANALYST_SQL_COMPILE",
                node_name="semantic_sql_compiler",
                status="OK",
                output_data={
                    "branchId": branch.branch_id,
                    "semanticView": branch.semantic_view,
                    "sqlHash": hashlib.sha256(compiled_sql.encode()).hexdigest(),
                },
                agent_id="data_analyst",
                run_id=run_id,
            )
        for attempt in range(1 if compiled_sql is not None else 2):
            try:
                sql = compiled_sql or await self._draft_sql(
                    question, branch_plan, branch=branch, feedback=feedback
                )
            except Exception:
                feedback = "SQL_DRAFT_FAILED"
                episode_service.record_step(
                    "DATA_ANALYST_SQL_GUARD",
                    node_name="sql_guard",
                    status="ERROR",
                    error_code=feedback,
                    output_data={
                        "branchId": branch.branch_id,
                        "attempt": attempt + 1,
                        "reason": feedback,
                    },
                    agent_id="data_analyst",
                    run_id=run_id,
                )
                continue
            guard = validate_sql(
                sql,
                max_days=settings.analytics_max_days,
                max_rows=settings.analytics_max_rows,
                expected_view=branch.semantic_view,
                expected_start_date=branch.start_date,
                expected_end_date=branch.end_date,
                access_policy=access_policy,
            )
            episode_service.record_step(
                "DATA_ANALYST_SQL_GUARD",
                node_name="sql_guard",
                status="OK" if guard.allowed else "BLOCKED",
                output_data={
                    "branchId": branch.branch_id,
                    "attempt": attempt + 1,
                    "reason": guard.reason,
                    "sqlHash": hashlib.sha256(sql.encode()).hexdigest(),
                    "sql": guard.sql if guard.allowed else None,
                    "lineage": list(guard.tables),
                },
                agent_id="data_analyst",
                run_id=run_id,
            )
            if not guard.allowed:
                feedback = guard.reason
                continue
            sql = guard.sql
            try:
                explain = await asyncio.wait_for(
                    _explain_sql(sql, settings.analytics_query_timeout_ms, cursor),
                    timeout=settings.analytics_query_timeout_ms / 1000,
                )
                scan_estimate = _explain_scan_estimate(explain)
                episode_service.record_step(
                    "DATA_ANALYST_EXPLAIN",
                    node_name="data_analyst_explain",
                    status="OK",
                    output_data={
                        "branchId": branch.branch_id,
                        "rows": explain[:10],
                        "scanEstimate": scan_estimate,
                        "sqlHash": hashlib.sha256(sql.encode()).hexdigest(),
                    },
                    agent_id="data_analyst",
                    run_id=run_id,
                )
                break
            except TimeoutError:
                feedback = "SQL_EXPLAIN_TIMEOUT"
            except Exception as exc:
                if _database_error_code(exc) == _EXPLAIN_VIEW_PRIVILEGE_ERROR:
                    # MySQL requires SELECT on a view's underlying tables for
                    # EXPLAIN even when the definer view itself is readable.
                    # Preserve the ten-view reader boundary and make the
                    # unavailable estimate explicit instead of pretending the
                    # plan scanned zero rows.
                    explain_diagnostic = {
                        "status": "UNAVAILABLE",
                        "reasonCode": _EXPLAIN_VIEW_PRIVILEGE_REASON,
                        "databaseErrorCode": _EXPLAIN_VIEW_PRIVILEGE_ERROR,
                        "attempted": True,
                    }
                    warnings.append(_EXPLAIN_VIEW_PRIVILEGE_REASON)
                    episode_service.record_step(
                        "DATA_ANALYST_EXPLAIN",
                        node_name="data_analyst_explain",
                        status="DEGRADED",
                        error_code=_EXPLAIN_VIEW_PRIVILEGE_REASON,
                        output_data={
                            "branchId": branch.branch_id,
                            "rows": [],
                            "scanEstimate": None,
                            "explainDiagnostic": explain_diagnostic,
                            "sqlHash": hashlib.sha256(sql.encode()).hexdigest(),
                        },
                        agent_id="data_analyst",
                        run_id=run_id,
                    )
                    break
                if str(exc) == "analytics DB pool not initialized":
                    return {
                        "branchId": branch.branch_id,
                        "purpose": branch.purpose,
                        "status": "ANALYTICS_POOL_UNAVAILABLE",
                        "sql": sql,
                        "sqlSource": sql_source,
                        "lineage": list(guard.tables),
                        "warnings": ["ANALYTICS_POOL_UNAVAILABLE"],
                    }
                feedback = "SQL_EXPLAIN_FAILED"
            episode_service.record_step(
                "DATA_ANALYST_EXPLAIN",
                node_name="data_analyst_explain",
                status="ERROR",
                error_code=feedback,
                output_data={
                    "branchId": branch.branch_id,
                    "attempt": attempt + 1,
                    "sqlHash": hashlib.sha256(sql.encode()).hexdigest(),
                },
                agent_id="data_analyst",
                run_id=run_id,
            )
        else:
            reason = feedback or "SQL_REJECTED"
            return {
                "branchId": branch.branch_id,
                "purpose": branch.purpose,
                "status": reason,
                "sql": sql,
                "sqlSource": sql_source,
                "lineage": list(guard.tables if guard else ()),
                "warnings": [reason, *warnings],
            }

        query_started = time.perf_counter()
        try:
            raw_rows = (
                await asyncio.wait_for(
                    _execute_sql(sql, settings.analytics_query_timeout_ms, cursor),
                    timeout=settings.analytics_query_timeout_ms / 1000,
                )
            )[: settings.analytics_max_rows]
        except TimeoutError:
            status = "QUERY_TIMEOUT"
            raw_rows = []
        except Exception:
            status = "DATABASE_UNAVAILABLE"
            raw_rows = []
        else:
            status = "SUCCEEDED" if raw_rows else "EMPTY_RESULT"
        columns = list(raw_rows[0]) if raw_rows else [*branch.dimensions, *branch.metrics]
        rows, column_types = normalize_typed_rows(
            raw_rows, view=branch.semantic_view, columns=columns
        )
        episode_service.record_step(
            "DATA_ANALYST_QUERY",
            node_name="data_analyst_query",
            status="OK" if status in {"SUCCEEDED", "EMPTY_RESULT"} else "ERROR",
            error_code=None if status in {"SUCCEEDED", "EMPTY_RESULT"} else status,
            output_data={"branchId": branch.branch_id, "rowCount": len(rows)},
            latency_ms=round((time.perf_counter() - query_started) * 1000),
            agent_id="data_analyst",
            run_id=run_id,
        )
        if status not in {"SUCCEEDED", "EMPTY_RESULT"}:
            return {
                "branchId": branch.branch_id,
                "purpose": branch.purpose,
                "status": status,
                "sql": sql,
                "lineage": list(guard.tables if guard else ()),
                "warnings": [status, *warnings],
                "explain": explain[:10],
                "explainDiagnostic": explain_diagnostic,
                "scanEstimate": scan_estimate,
            }

        narrative = await self._narrative(question, branch_plan, raw_rows)
        return {
            "branchId": branch.branch_id,
            "purpose": branch.purpose,
            "status": status,
            "answer": narrative.answer,
            "highlights": narrative.highlights,
            "sql": sql,
            "sqlSource": sql_source,
            "columns": columns[:20],
            "columnTypes": column_types,
            "rows": rows,
            "chart": self._chart(columns, raw_rows),
            "metricDefinitions": _metric_definitions(branch_plan),
            "lineage": list(guard.tables if guard else ()),
            "warnings": warnings,
            "explain": explain[:10],
            "explainDiagnostic": explain_diagnostic,
            "scanEstimate": scan_estimate,
        }

    async def _ask_metric_tree(
        self,
        question: str,
        plan: DataAnalysisPlan,
        *,
        run_id: str,
        started: float,
        cursor: Any | None = None,
        data_as_of: str = "",
        admin_id: str = "",
        permissions: Iterable[str] = (),
        tenant_id: str | None = None,
        page_size: int = _DEFAULT_ANALYTICS_PAGE_SIZE,
        access_policy: AnalyticsAccessPolicy | None = None,
    ) -> dict[str, Any]:
        async def execute_safely(branch: DataAnalysisBranch) -> dict[str, Any]:
            """Keep one failed branch from cancelling independent diagnostics."""
            try:
                branch_kwargs = {"run_id": run_id, "cursor": cursor}
                if access_policy is not None:
                    branch_kwargs["access_policy"] = access_policy
                return await self._execute_metric_branch(question, plan, branch, **branch_kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status = "DATA_ANALYST_BRANCH_FAILED"
                episode_service.record_step(
                    "DATA_ANALYST_BRANCH_ERROR",
                    node_name="data_analyst_branch",
                    status="ERROR",
                    error_code=status,
                    output_data={
                        "branchId": branch.branch_id,
                        "errorType": type(exc).__name__,
                    },
                    agent_id="data_analyst",
                    run_id=run_id,
                )
                return {
                    "branchId": branch.branch_id,
                    "purpose": branch.purpose,
                    "status": status,
                    "lineage": [branch.semantic_view],
                    "warnings": [status],
                }

        # All branches execute sequentially on the same read-only consistent
        # snapshot.  This prevents cross-branch drift and avoids concurrent use
        # of one aiomysql cursor.
        branch_results = []
        for branch in plan.branches[:3]:
            branch_results.append(await execute_safely(branch))
        successful = [
            branch
            for branch in branch_results
            if branch.get("status") in {"SUCCEEDED", "EMPTY_RESULT"}
        ]
        warnings = list(
            dict.fromkeys(
                str(warning)
                for branch in branch_results
                for warning in branch.get("warnings") or []
            )
        )
        if not successful:
            status = str(branch_results[0].get("status") or "DATA_ANALYST_BRANCH_FAILED")
            episode_service.finish_run(
                "metric_tree_failed", run_id=run_id, status="FAILED", force_keep=True
            )
            return {
                "runId": run_id,
                "outcome": None,
                "completion": "FAILED",
                "status": status,
                "branches": branch_results,
                "warnings": warnings or [status],
                "lineage": list(
                    dict.fromkeys(
                        view for branch in branch_results for view in branch.get("lineage") or []
                    )
                ),
            }

        first = successful[0]
        causal_caution = _CAUSAL_CAUTION if _CAUSAL_QUESTION.search(question) else None
        answers = [
            f"{branch.get('purpose') or branch.get('branchId')}：{branch.get('answer')}"
            for branch in successful
            if branch.get("answer")
        ]
        if causal_caution:
            answers.append(causal_caution)
        lineage = list(
            dict.fromkeys(view for branch in branch_results for view in branch.get("lineage") or [])
        )
        status = (
            "SUCCEEDED"
            if any(branch.get("status") == "SUCCEEDED" for branch in successful)
            else "EMPTY_RESULT"
        )
        if len(successful) != len(branch_results):
            warnings.append("PARTIAL_METRIC_TREE")
        latency_ms = round((time.perf_counter() - started) * 1000)
        episode_service.record_step(
            "DATA_ANALYST_RESULT",
            node_name="data_analyst_result",
            status="DEGRADED" if len(successful) != len(branch_results) else "OK",
            output_data={
                "branchCount": len(branch_results),
                "successfulBranchCount": len(successful),
                "lineage": lineage,
                "latencyMs": latency_ms,
                "answerVersion": _DATA_ANALYST_VERSION,
                "causalCaution": bool(causal_caution),
            },
            latency_ms=latency_ms,
            agent_id="data_analyst",
            run_id=run_id,
        )
        episode_service.finish_run(
            "ok" if len(successful) == len(branch_results) else "partial_metric_tree",
            run_id=run_id,
            status="SUCCEEDED" if len(successful) == len(branch_results) else "DEGRADED",
            latency_ms=latency_ms,
            force_keep=True,
        )
        result = {
            "runId": run_id,
            "status": status,
            "answer": "\n".join(answers),
            "highlights": [
                item for branch in successful for item in branch.get("highlights") or []
            ][:8],
            "branches": branch_results,
            "diagnosisTree": [
                {
                    "branchId": branch.get("branchId"),
                    "purpose": branch.get("purpose"),
                    "status": branch.get("status"),
                    "lineage": branch.get("lineage") or [],
                }
                for branch in branch_results
            ],
            "sql": first.get("sql"),
            "queries": [
                {
                    "branchId": branch.get("branchId"),
                    "purpose": branch.get("purpose"),
                    "status": branch.get("status"),
                    "sql": branch.get("sql"),
                    "sqlSource": branch.get("sqlSource"),
                    "explain": branch.get("explain") or [],
                    "explainDiagnostic": branch.get("explainDiagnostic"),
                    "scanEstimate": branch.get("scanEstimate"),
                    "lineage": branch.get("lineage") or [],
                }
                for branch in branch_results
            ],
            "columns": first.get("columns") or [],
            "columnTypes": first.get("columnTypes") or {},
            "rows": first.get("rows") or [],
            "chart": first.get("chart"),
            "metricDefinitions": _metric_tree_definitions(plan),
            "interpretation": plan.interpretation,
            "lineage": lineage,
            "warnings": list(dict.fromkeys(warnings)),
            "explain": first.get("explain") or [],
            "explainDiagnostic": first.get("explainDiagnostic"),
            "scanEstimate": first.get("scanEstimate"),
            "causalCaution": causal_caution,
            "latencyMs": latency_ms,
        }
        return await self._finalize_answer(
            result,
            plan,
            admin_id=admin_id,
            permissions=permissions,
            tenant_id=tenant_id,
            data_as_of=data_as_of,
            page_size=page_size,
        )

    async def _ask_foundation_within_budget(
        self,
        question: str,
        *,
        admin_id: str,
        permissions: tuple[str, ...],
        tenant_id: str | None,
        access_policy: AnalyticsAccessPolicy | None,
        page_size: int,
        run_id: str,
        started: float,
        allow_clarification: bool,
    ) -> dict[str, Any]:
        """Execute one governed request against one database snapshot."""

        episode_service.start_run(
            run_id=run_id,
            message_id=None,
            user_id=f"admin:{admin_id}"[:32],
            session_id=None,
            intent="DATA_ANALYST",
            queue_name="admin.data_analyst",
            force_keep=True,
            agent_id="data_analyst",
            agent_version=_DATA_ANALYST_VERSION,
            actor_type="ADMIN",
        )
        with bind_episode(
            run_id,
            message_id=None,
            user_id=f"admin:{admin_id}",
            force_keep=True,
        ):
            policy = evaluate_question_policy(question, tenant_id=tenant_id)
            if policy is not None:
                contract = analytics_no_query_contract(policy.required_fact or policy.answer)
                answer = (
                    f"{policy.answer}\n{contract['capabilityBoundary']}\n"
                    "本次 no SQL：未执行查询，也未读取分析数据。"
                )
                episode_service.record_step(
                    "DATA_ANALYST_POLICY",
                    node_name="data_analyst_policy",
                    status="BLOCKED",
                    error_code=policy.reason_code,
                    output_data={"outcome": policy.outcome},
                    agent_id="data_analyst",
                    run_id=run_id,
                )
                episode_service.finish_run(
                    policy.reason_code.lower(),
                    run_id=run_id,
                    status="FAILED" if policy.outcome == "DENY" else "DEGRADED",
                    force_keep=True,
                )
                return {
                    "runId": run_id,
                    "outcome": policy.outcome,
                    "completion": "NOT_APPLICABLE",
                    "status": policy.reason_code,
                    "reasonCode": policy.reason_code,
                    "answer": answer,
                    "warnings": [],
                    "_httpStatus": policy.http_status,
                    **contract,
                }

            try:
                plan = await self._plan(question)
            except Exception as exc:
                code = (
                    str(exc)
                    if str(exc).startswith("DATA_ANALYST_")
                    else "DATA_ANALYST_MODEL_UNAVAILABLE"
                )
                episode_service.record_step(
                    "DATA_ANALYST_PLAN",
                    node_name="data_analyst_plan",
                    status="ERROR",
                    error_code=code,
                    output_data={"status": code, "errorType": type(exc).__name__},
                    agent_id="data_analyst",
                    run_id=run_id,
                )
                episode_service.finish_run(
                    "planning_failed",
                    run_id=run_id,
                    status="FAILED",
                    force_keep=True,
                )
                return {
                    "runId": run_id,
                    "outcome": None,
                    "completion": "FAILED",
                    "status": code,
                    "warnings": [code],
                }

            episode_service.record_step(
                "DATA_ANALYST_PLAN",
                node_name="data_analyst_plan",
                status="OK",
                output_data=plan.model_dump(mode="json"),
                agent_id="data_analyst",
                run_id=run_id,
            )
            try:
                for branch in plan.branches:
                    _compile_branch_sql(branch)
            except SemanticPlanUnsupported:
                reason_code = "SEMANTIC_PLAN_UNSUPPORTED"
                contract = analytics_no_query_contract(
                    "deterministic compiler boundary",
                    "no free SQL fallback",
                )
                episode_service.record_step(
                    "DATA_ANALYST_SQL_COMPILE",
                    node_name="semantic_sql_compiler",
                    status="BLOCKED",
                    error_code=reason_code,
                    output_data={"reason": reason_code},
                    agent_id="data_analyst",
                    run_id=run_id,
                )
                episode_service.finish_run(
                    "semantic_plan_unsupported",
                    run_id=run_id,
                    status="DEGRADED",
                    force_keep=True,
                )
                return {
                    "runId": run_id,
                    "outcome": "ABSTAIN",
                    "completion": "NOT_APPLICABLE",
                    "status": reason_code,
                    "reasonCode": reason_code,
                    "answer": (
                        "该分析语义计划暂不在确定性编译器支持范围内，已停止执行，"
                        "不会回退到自由 SQL。\n"
                        f"{contract['capabilityBoundary']}\n"
                        "本次 no SQL：未执行查询，也未读取分析数据。"
                    ),
                    "warnings": [],
                    **contract,
                }
            if plan.status == "NEEDS_CLARIFICATION":
                if not allow_clarification:
                    reason_code = "AMBIGUITY_REMAINS_AFTER_CLARIFICATION"
                    contract = analytics_no_query_contract("一次澄清后仍存在关键歧义")
                    episode_service.finish_run(
                        "clarification_exhausted",
                        run_id=run_id,
                        status="DEGRADED",
                        force_keep=True,
                    )
                    return {
                        "runId": run_id,
                        "outcome": "ABSTAIN",
                        "completion": "NOT_APPLICABLE",
                        "status": reason_code,
                        "reasonCode": reason_code,
                        "answer": (
                            "一次澄清后仍存在关键歧义，当前请求不执行查询。\n"
                            f"{contract['capabilityBoundary']}\n"
                            "本次 no SQL：未执行查询，也未读取分析数据。"
                        ),
                        "warnings": [],
                        **contract,
                    }
                options = [
                    {
                        "choiceId": option.choice_id,
                        "label": option.label,
                        "answerSuffix": option.answer_suffix,
                    }
                    for option in plan.clarification_options
                ]
                try:
                    clarification = await analytics_clarification_service.issue(
                        question=question,
                        clarification_question=str(plan.clarification_question or "请确认统计口径"),
                        options=options,
                        admin_id=admin_id,
                        permissions=permissions,
                        tenant_id=tenant_id,
                        run_id=run_id,
                    )
                except AnalyticsResultError:
                    episode_service.finish_run(
                        "clarification_state_failed",
                        run_id=run_id,
                        status="FAILED",
                        force_keep=True,
                    )
                    raise
                episode_service.finish_run(
                    "needs_clarification",
                    run_id=run_id,
                    status="DEGRADED",
                    force_keep=True,
                )
                return {
                    "runId": run_id,
                    "outcome": "CLARIFY",
                    "completion": "NOT_APPLICABLE",
                    "status": "NEEDS_CLARIFICATION",
                    "clarificationQuestion": plan.clarification_question,
                    "answer": plan.clarification_question,
                    "warnings": [],
                    **analytics_no_query_contract(
                        "structuredOptions",
                        "ownerBoundToken",
                        "tokenTtl=900",
                    ),
                    **clarification,
                }

            try:
                async with acquire_analytics_snapshot() as snapshot:
                    if len(plan.branches) > 1:
                        return await self._ask_metric_tree(
                            question,
                            plan,
                            run_id=run_id,
                            started=started,
                            cursor=snapshot.cursor,
                            data_as_of=snapshot.data_as_of,
                            admin_id=admin_id,
                            permissions=permissions,
                            tenant_id=tenant_id,
                            page_size=page_size,
                            access_policy=access_policy,
                        )
                    branch = plan.branches[0]
                    branch_result = await self._execute_metric_branch(
                        question,
                        plan,
                        branch,
                        run_id=run_id,
                        cursor=snapshot.cursor,
                        access_policy=access_policy,
                    )
                    data_as_of = snapshot.data_as_of
            except RuntimeError as exc:
                status = (
                    "ANALYTICS_POOL_UNAVAILABLE"
                    if str(exc) == "analytics DB pool not initialized"
                    else "DATABASE_UNAVAILABLE"
                )
                episode_service.finish_run(
                    "database_unavailable",
                    run_id=run_id,
                    status="FAILED",
                    force_keep=True,
                )
                return {
                    "runId": run_id,
                    "outcome": None,
                    "completion": "FAILED",
                    "status": status,
                    "warnings": [status],
                }
            except Exception as exc:
                episode_service.record_step(
                    "DATA_ANALYST_QUERY",
                    node_name="data_analyst_query",
                    status="ERROR",
                    error_code="DATABASE_UNAVAILABLE",
                    output_data={"errorType": type(exc).__name__},
                    agent_id="data_analyst",
                    run_id=run_id,
                )
                episode_service.finish_run(
                    "database_unavailable",
                    run_id=run_id,
                    status="FAILED",
                    force_keep=True,
                )
                return {
                    "runId": run_id,
                    "outcome": None,
                    "completion": "FAILED",
                    "status": "DATABASE_UNAVAILABLE",
                    "warnings": ["DATABASE_UNAVAILABLE"],
                }

            if branch_result.get("status") not in {"SUCCEEDED", "EMPTY_RESULT"}:
                status = str(branch_result.get("status") or "DATA_ANALYST_BRANCH_FAILED")
                episode_service.finish_run(
                    "single_branch_failed",
                    run_id=run_id,
                    status="FAILED",
                    force_keep=True,
                )
                return {
                    "runId": run_id,
                    "outcome": None,
                    "completion": "FAILED",
                    "status": status,
                    "sql": branch_result.get("sql"),
                    "lineage": branch_result.get("lineage") or [],
                    "explain": branch_result.get("explain") or [],
                    "warnings": branch_result.get("warnings") or [status],
                }

            causal_caution = _CAUSAL_CAUTION if _CAUSAL_QUESTION.search(question) else None
            answer = str(branch_result.get("answer") or "")
            if causal_caution and causal_caution not in answer:
                answer = f"{answer}\n{causal_caution}".strip()
            latency_ms = round((time.perf_counter() - started) * 1000)
            result = {
                "runId": run_id,
                "status": branch_result.get("status"),
                "answer": answer,
                "highlights": branch_result.get("highlights") or [],
                "sql": branch_result.get("sql"),
                "sqlSource": branch_result.get("sqlSource"),
                "queries": [
                    {
                        "branchId": branch.branch_id,
                        "purpose": branch.purpose,
                        "status": branch_result.get("status"),
                        "sql": branch_result.get("sql"),
                        "sqlSource": branch_result.get("sqlSource"),
                        "explain": branch_result.get("explain") or [],
                        "explainDiagnostic": branch_result.get("explainDiagnostic"),
                        "scanEstimate": branch_result.get("scanEstimate"),
                        "lineage": branch_result.get("lineage") or [],
                    }
                ],
                "columns": branch_result.get("columns") or [],
                "columnTypes": branch_result.get("columnTypes") or {},
                "rows": branch_result.get("rows") or [],
                "chart": branch_result.get("chart"),
                "metricDefinitions": branch_result.get("metricDefinitions") or [],
                "interpretation": plan.interpretation,
                "lineage": branch_result.get("lineage") or [],
                "warnings": branch_result.get("warnings") or [],
                "explain": branch_result.get("explain") or [],
                "explainDiagnostic": branch_result.get("explainDiagnostic"),
                "scanEstimate": branch_result.get("scanEstimate"),
                "causalCaution": causal_caution,
                "latencyMs": latency_ms,
            }
            episode_service.record_step(
                "DATA_ANALYST_RESULT",
                node_name="data_analyst_result",
                status="OK",
                output_data={
                    "rowCount": len(result["rows"]),
                    "lineage": result["lineage"],
                    "latencyMs": latency_ms,
                    "answerVersion": _DATA_ANALYST_VERSION,
                },
                latency_ms=latency_ms,
                agent_id="data_analyst",
                run_id=run_id,
            )
            episode_service.finish_run(
                "ok" if result["rows"] else "empty_result",
                run_id=run_id,
                status="SUCCEEDED",
                latency_ms=latency_ms,
                force_keep=True,
            )
            return await self._finalize_answer(
                result,
                plan,
                admin_id=admin_id,
                permissions=permissions,
                tenant_id=tenant_id,
                data_as_of=data_as_of,
                page_size=page_size,
            )

    async def ask(
        self,
        question: str,
        *,
        admin_id: str,
        permissions: Iterable[str] | None = None,
        tenant_id: str | None = None,
        cursor: str | None = None,
        page_size: int | None = None,
        allow_clarification: bool = True,
    ) -> dict:
        settings = get_settings()
        if not settings.data_analyst_enabled:
            return {
                "outcome": None,
                "completion": "FAILED",
                "status": "DISABLED",
                "warnings": ["DATA_ANALYST_ENABLED=false"],
            }
        permission_tuple = tuple(str(item) for item in (permissions or ()))
        requested_page_size = page_size or _DEFAULT_ANALYTICS_PAGE_SIZE
        try:
            requested_page_size = int(requested_page_size)
        except (TypeError, ValueError):
            requested_page_size = _DEFAULT_ANALYTICS_PAGE_SIZE
        requested_page_size = max(
            1,
            min(requested_page_size, int(getattr(settings, "analytics_max_rows", 200))),
        )
        if cursor:
            return await analytics_result_service.page(
                cursor,
                admin_id=admin_id,
                permissions=permission_tuple,
                tenant_id=tenant_id,
                page_size=requested_page_size,
            )
        question = str(question or "").strip()
        if not question or len(question) > 500:
            return {
                "outcome": None,
                "completion": "FAILED",
                "status": "INVALID_QUESTION",
                "warnings": ["问题不能为空且不超过500字"],
            }

        run_id = uuid.uuid4().hex
        started = time.perf_counter()
        access_policy = (
            AnalyticsAccessPolicy.from_permissions(permission_tuple, tenant_id=tenant_id)
            if permissions is not None
            else None
        )
        try:
            result = await asyncio.wait_for(
                self._ask_foundation_within_budget(
                    question,
                    admin_id=admin_id,
                    permissions=permission_tuple,
                    tenant_id=tenant_id,
                    access_policy=access_policy,
                    page_size=requested_page_size,
                    run_id=run_id,
                    started=started,
                    allow_clarification=allow_clarification,
                ),
                timeout=settings.analytics_request_timeout_seconds,
            )
            contract_errors = _response_contract_errors(result)
            if contract_errors:
                episode_service.record_step(
                    "DATA_ANALYST_RESPONSE_CONTRACT",
                    node_name="data_analyst_response_contract",
                    status="BLOCKED",
                    error_code="ANALYTICS_RESPONSE_CONTRACT_FAILED",
                    output_data={"violations": contract_errors},
                    agent_id="data_analyst",
                    run_id=run_id,
                )
                return _contract_failure(result, contract_errors)
            return result
        except TimeoutError:
            latency_ms = round((time.perf_counter() - started) * 1000)
            episode_service.record_step(
                "DATA_ANALYST_TIMEOUT",
                node_name="data_analyst_timeout",
                status="ERROR",
                error_code="DATA_ANALYST_REQUEST_TIMEOUT",
                output_data={"budgetSeconds": settings.analytics_request_timeout_seconds},
                latency_ms=latency_ms,
                agent_id="data_analyst",
                run_id=run_id,
            )
            episode_service.finish_run(
                "request_timeout",
                run_id=run_id,
                status="FAILED",
                latency_ms=latency_ms,
                force_keep=True,
            )
            return {
                "runId": run_id,
                "outcome": None,
                "completion": "FAILED",
                "status": "DATA_ANALYST_REQUEST_TIMEOUT",
                "warnings": ["分析超过整体超时预算，请缩小问题范围后重试"],
            }


data_analyst_service = DataAnalystService()
