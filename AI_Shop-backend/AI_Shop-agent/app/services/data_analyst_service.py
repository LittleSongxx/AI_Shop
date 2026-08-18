from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import re
import time
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from numbers import Number
from typing import Any, Iterable, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator, model_validator

from app.config.settings import get_settings
from app.db.analytics_pool import acquire_analytics
from app.observability.llm_metrics import invoke_llm_with_metrics
from app.services.analytics_catalog import CATALOG, allowed_plan_fields, catalog_prompt
from app.services.episode_service import bind_episode, episode_service
from app.services.llm_factory import create_memory_llm
from app.services.sql_guard import (
    AnalyticsAccessPolicy,
    SqlGuardResult,
    validate_sql,
)


class DataAnalysisBranch(BaseModel):
    branch_id: str = Field(min_length=1, max_length=64)
    purpose: str = Field(default="", max_length=300)
    semantic_view: str
    metrics: list[str] = Field(default_factory=list, min_length=1, max_length=8)
    dimensions: list[str] = Field(default_factory=list, max_length=5)
    start_date: date | None = None
    end_date: date | None = None


class DataAnalysisPlan(BaseModel):
    status: Literal["READY", "NEEDS_CLARIFICATION"] = "READY"
    semantic_view: str | None = None
    metrics: list[str] = Field(default_factory=list, max_length=8)
    dimensions: list[str] = Field(default_factory=list, max_length=5)
    start_date: date | None = None
    end_date: date | None = None
    interpretation: str = ""
    clarification_question: str | None = None
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
            self.branches = []
            return self
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


_AMBIGUOUS_SALES = re.compile(r"(销量最高|最畅销|最好卖|销售最好|卖(?:得|的)?最好)")
_EXPLICIT_SALES_METRIC = re.compile(r"(销售额|金额|件数|数量|订单数|订单量)")
_CAUSAL_QUESTION = re.compile(r"(为什么|原因|导致|归因|怎么下降|为何|影响因素)")
_CAUSAL_CAUTION = "相关性不等于因果关系；以下结果只用于定位待验证假设。"
_DEFAULT_ANALYTICS_PAGE_SIZE = 50


def _question_dates(question: str) -> tuple[date, date]:
    match = re.search(r"最近\s*(\d+)\s*天", question)
    days = min(90, max(1, int(match.group(1) if match else 7)))
    end = date.today()
    return end - timedelta(days=days - 1), end


def _metric_definitions(plan: DataAnalysisPlan) -> list[dict[str, str]]:
    if not plan.semantic_view or plan.semantic_view not in CATALOG:
        return []
    item = CATALOG[plan.semantic_view]
    definitions = dict(item["columns"])
    definitions.update(
        {
            name: spec.get("definition")
            for name, spec in (item.get("derived_metrics") or {}).items()
        }
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


async def _explain_sql(sql: str, timeout_ms: int) -> list[dict]:
    async with acquire_analytics() as cursor:
        await cursor.execute("SET SESSION MAX_EXECUTION_TIME=%s", (timeout_ms,))
        await cursor.execute("EXPLAIN " + sql)
        return list(await cursor.fetchall())


async def _execute_sql(sql: str, timeout_ms: int) -> list[dict]:
    async with acquire_analytics() as cursor:
        await cursor.execute("SET SESSION MAX_EXECUTION_TIME=%s", (timeout_ms,))
        await cursor.execute(sql)
        return list(await cursor.fetchall())


def _database_error_code(exc: Exception) -> int | None:
    args = getattr(exc, "args", ())
    return int(args[0]) if args and isinstance(args[0], int) else None


def _normalize_analytics_rows(rows: list[dict]) -> list[dict]:
    """Return JSON-native values without turning numeric metrics into strings."""
    normalized: list[dict] = []
    for row in rows:
        item = {}
        for key, value in row.items():
            if isinstance(value, Decimal):
                item[key] = float(value)
            elif isinstance(value, (date, datetime)):
                item[key] = value.isoformat()
            else:
                item[key] = value
        normalized.append(item)
    return normalized


def _json_size(value: object) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def _cursor_secret(settings: object) -> bytes:
    return str(getattr(settings, "internal_token", "aishop-development-cursor-secret")).encode(
        "utf-8"
    )


def _encode_result_cursor(
    *,
    settings: object,
    admin_id: str,
    sql_hash: str,
    offset: int,
) -> str:
    payload = {
        "v": 1,
        "owner": hashlib.sha256(admin_id.encode("utf-8")).hexdigest(),
        "sqlHash": sql_hash,
        "offset": max(0, int(offset)),
        "expiresAt": int(time.time())
        + int(getattr(settings, "analytics_cursor_ttl_seconds", 900)),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        _cursor_secret(settings), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{encoded}.{signature}"


def _decode_result_cursor(
    token: str,
    *,
    settings: object,
    admin_id: str,
    sql_hash: str,
) -> tuple[int | None, str | None]:
    encoded, separator, signature = str(token or "").partition(".")
    if not separator or not encoded or not signature:
        return None, "CURSOR_INVALID"
    try:
        expected = hmac.new(
            _cursor_secret(settings), encoded.encode("ascii"), hashlib.sha256
        ).hexdigest()
    except UnicodeEncodeError:
        return None, "CURSOR_INVALID"
    if not hmac.compare_digest(expected, signature):
        return None, "CURSOR_INVALID"
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (
        binascii.Error,
        UnicodeDecodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        return None, "CURSOR_INVALID"
    if not isinstance(payload, dict):
        return None, "CURSOR_INVALID"
    if payload.get("v") != 1:
        return None, "CURSOR_INVALID"
    try:
        expires_at = int(payload.get("expiresAt") or 0)
    except (TypeError, ValueError, OverflowError):
        return None, "CURSOR_INVALID"
    if expires_at < int(time.time()):
        return None, "CURSOR_EXPIRED"
    if payload.get("owner") != hashlib.sha256(admin_id.encode("utf-8")).hexdigest():
        return None, "CURSOR_OWNER_MISMATCH"
    if payload.get("sqlHash") != sql_hash:
        return None, "CURSOR_QUERY_MISMATCH"
    try:
        offset = int(payload.get("offset"))
    except (TypeError, ValueError):
        return None, "CURSOR_INVALID"
    return (offset if offset >= 0 else None), (None if offset >= 0 else "CURSOR_INVALID")


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


def _format_metric_value(value: Number) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.4f}".rstrip("0").rstrip(".")


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
                isinstance(row.get(name), Number)
                and not isinstance(row.get(name), bool)
                for row in rows
            )
        ][:8]

    statements: list[str] = []
    highlights: list[str] = []
    for metric in metric_names[:8]:
        points = [
            (row.get(dimension) if dimension else None, row.get(metric))
            for row in rows
            if isinstance(row.get(metric), Number)
            and not isinstance(row.get(metric), bool)
        ]
        if not points:
            continue
        maximum = max(points, key=lambda item: float(item[1]))
        minimum = min(points, key=lambda item: float(item[1]))
        label = _display_metric(plan, metric)
        dimension_suffix = (
            f"（{maximum[0]}）" if maximum[0] not in (None, "") else ""
        )
        if float(maximum[1]) == float(minimum[1]):
            statement = f"{label}在返回结果中均为{_format_metric_value(maximum[1])}"
        else:
            minimum_suffix = (
                f"（{minimum[0]}）" if minimum[0] not in (None, "") else ""
            )
            statement = (
                f"{label}最大值为{_format_metric_value(maximum[1])}{dimension_suffix}，"
                f"最小值为{_format_metric_value(minimum[1])}{minimum_suffix}"
            )
        statements.append(statement)
        highlights.append(
            f"{label}最大值：{_format_metric_value(maximum[1])}{dimension_suffix}"
        )

        if dimension in {"date", "snapshot_date"} and len(points) > 1:
            ordered = sorted(points, key=lambda item: str(item[0] or ""))
            first, last = ordered[0], ordered[-1]
            statements.append(
                f"{label}从{first[0]}的{_format_metric_value(first[1])}"
                f"变为{last[0]}的{_format_metric_value(last[1])}"
            )

    if dimension in {"date", "snapshot_date"} and plan.start_date and plan.end_date:
        expected_days = (plan.end_date - plan.start_date).days + 1
        observed_days = len(
            {
                str(row.get(dimension))
                for row in rows
                if row.get(dimension) not in (None, "")
            }
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
            if not branch.metrics or not set(selected).issubset(columns):
                raise ValueError("DATA_ANALYST_COLUMN_INVALID")
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
        if _AMBIGUOUS_SALES.search(question) and not _EXPLICIT_SALES_METRIC.search(question):
            return DataAnalysisPlan(
                status="NEEDS_CLARIFICATION",
                clarification_question="你希望按销售金额、销售件数还是订单数判断“销量最高”？",
                interpretation="销售排名口径存在歧义",
            )
        start, end = _question_dates(question)
        messages = [
            SystemMessage(
                content=(
                    "你是电商经营分析规划 Agent。只选择下方语义视图、字段和受治理派生指标，"
                    "不得假设原始表。问题有业务口径歧义时返回 NEEDS_CLARIFICATION。"
                    "复杂问题拆成最多三个相互独立的指标树分支；每个分支只能选择一个语义视图，"
                    "使用唯一 branch_id，并写清该分支验证什么。每个 READY 分支的 metrics 至少一项；"
                    "派生指标必须使用目录中的指标名。简单问题只生成一个分支。"
                    "严格只返回一个 JSON 对象，不得输出 Markdown。"
                    f"JSON Schema：{_schema_instruction(DataAnalysisPlan)}"
                )
            ),
            HumanMessage(
                content=(
                    f"今天={date.today().isoformat()}；默认时间范围="
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
            parsing_error = (
                response.get("parsing_error") if isinstance(response, dict) else None
            )
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
                            "必须包含至少一个目录允许的 metrics，缺货 SKU 数量使用受治理派生指标"
                            " stockout_sku_count。"
                        )
                    )
                )
        if plan is None:
            raise ValueError("DATA_ANALYST_PLAN_PARSE_FAILED")
        if plan.status == "NEEDS_CLARIFICATION":
            if not str(plan.clarification_question or "").strip():
                plan.clarification_question = "请明确要使用的业务指标和统计口径。"
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
    def _branch_plan(
        plan: DataAnalysisPlan, branch: DataAnalysisBranch
    ) -> DataAnalysisPlan:
        return DataAnalysisPlan(
            semantic_view=branch.semantic_view,
            metrics=list(branch.metrics),
            dimensions=list(branch.dimensions),
            start_date=branch.start_date,
            end_date=branch.end_date,
            interpretation=branch.purpose or plan.interpretation,
        )

    async def _execute_metric_branch(
        self,
        question: str,
        plan: DataAnalysisPlan,
        branch: DataAnalysisBranch,
        *,
        run_id: str,
        access_policy: AnalyticsAccessPolicy | None = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        branch_plan = self._branch_plan(plan, branch)
        sql = ""
        guard: SqlGuardResult | None = None
        explain: list[dict] = []
        warnings: list[str] = []
        feedback: str | None = None
        for attempt in range(2):
            try:
                sql = await self._draft_sql(
                    question,
                    branch_plan,
                    branch=branch,
                    feedback=feedback,
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
                    _explain_sql(sql, settings.analytics_query_timeout_ms),
                    timeout=settings.analytics_query_timeout_ms / 1000,
                )
                episode_service.record_step(
                    "DATA_ANALYST_EXPLAIN",
                    node_name="data_analyst_explain",
                    status="OK",
                    output_data={
                        "branchId": branch.branch_id,
                        "rows": explain[:10],
                        "sqlHash": hashlib.sha256(sql.encode()).hexdigest(),
                    },
                    agent_id="data_analyst",
                    run_id=run_id,
                )
                break
            except TimeoutError:
                feedback = "SQL_EXPLAIN_TIMEOUT"
            except RuntimeError as exc:
                if str(exc) == "analytics DB pool not initialized":
                    return {
                        "branchId": branch.branch_id,
                        "purpose": branch.purpose,
                        "status": "ANALYTICS_POOL_UNAVAILABLE",
                        "sql": sql,
                        "lineage": list(guard.tables),
                        "warnings": ["ANALYTICS_POOL_UNAVAILABLE"],
                    }
                feedback = "SQL_EXPLAIN_FAILED"
            except Exception as exc:
                if _database_error_code(exc) == 1345:
                    warning = "EXPLAIN_SKIPPED_VIEW_PRIVILEGE"
                    warnings.append(warning)
                    explain = [
                        {
                            "status": "SKIPPED",
                            "reason": "VIEW_DEFINER_PRIVILEGE_BOUNDARY",
                        }
                    ]
                    episode_service.record_step(
                        "DATA_ANALYST_EXPLAIN",
                        node_name="data_analyst_explain",
                        status="DEGRADED",
                        error_code=warning,
                        output_data={
                            "branchId": branch.branch_id,
                            "sqlHash": hashlib.sha256(sql.encode()).hexdigest(),
                            "reason": "VIEW_DEFINER_PRIVILEGE_BOUNDARY",
                        },
                        agent_id="data_analyst",
                        run_id=run_id,
                    )
                    break
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
                "lineage": list(guard.tables if guard else ()),
                "warnings": [reason, *warnings],
            }

        query_started = time.perf_counter()
        try:
            rows = _normalize_analytics_rows(
                (
                    await asyncio.wait_for(
                        _execute_sql(sql, settings.analytics_query_timeout_ms),
                        timeout=settings.analytics_query_timeout_ms / 1000,
                    )
                )[: settings.analytics_max_rows]
            )
        except TimeoutError:
            status = "QUERY_TIMEOUT"
            rows = []
        except Exception:
            status = "DATABASE_UNAVAILABLE"
            rows = []
        else:
            status = "SUCCEEDED" if rows else "EMPTY_RESULT"
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
            }

        columns = list(rows[0]) if rows else [*branch.dimensions, *branch.metrics]
        narrative = await self._narrative(question, branch_plan, rows)
        return {
            "branchId": branch.branch_id,
            "purpose": branch.purpose,
            "status": status,
            "answer": narrative.answer,
            "highlights": narrative.highlights,
            "sql": sql,
            "columns": columns[:20],
            "rows": rows,
            "chart": self._chart(columns, rows),
            "metricDefinitions": _metric_definitions(branch_plan),
            "lineage": list(guard.tables if guard else ()),
            "warnings": warnings,
            "explain": explain[:10],
        }

    async def _ask_metric_tree(
        self,
        question: str,
        plan: DataAnalysisPlan,
        *,
        run_id: str,
        started: float,
        access_policy: AnalyticsAccessPolicy | None = None,
    ) -> dict[str, Any]:
        async def execute_safely(branch: DataAnalysisBranch) -> dict[str, Any]:
            """Keep one failed branch from cancelling independent diagnostics."""
            try:
                branch_kwargs = {"run_id": run_id}
                if access_policy is not None:
                    branch_kwargs["access_policy"] = access_policy
                return await self._execute_metric_branch(
                    question, plan, branch, **branch_kwargs
                )
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

        # asyncio.gather preserves input order, so replay and UI ordering stay
        # deterministic even though independent SQL calls run in parallel.
        branch_results = list(
            await asyncio.gather(
                *(execute_safely(branch) for branch in plan.branches[:3])
            )
        )
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
                "status": status,
                "branches": branch_results,
                "warnings": warnings or [status],
                "lineage": list(
                    dict.fromkeys(
                        view
                        for branch in branch_results
                        for view in branch.get("lineage") or []
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
            dict.fromkeys(
                view for branch in branch_results for view in branch.get("lineage") or []
            )
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
                "answerVersion": "v2-metric-tree",
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
        return {
            "runId": run_id,
            "status": status,
            "answer": "\n".join(answers),
            "highlights": [
                item
                for branch in successful
                for item in branch.get("highlights") or []
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
                    "explain": branch.get("explain") or [],
                    "lineage": branch.get("lineage") or [],
                }
                for branch in branch_results
            ],
            "columns": first.get("columns") or [],
            "rows": first.get("rows") or [],
            "chart": first.get("chart"),
            "metricDefinitions": _metric_tree_definitions(plan),
            "interpretation": plan.interpretation,
            "lineage": lineage,
            "warnings": list(dict.fromkeys(warnings)),
            "explain": first.get("explain") or [],
            "causalCaution": causal_caution,
            "latencyMs": latency_ms,
        }

    async def ask(
        self,
        question: str,
        *,
        admin_id: str,
        permissions: Iterable[str] | None = None,
        tenant_id: str | None = None,
        cursor: str | None = None,
        page_size: int | None = None,
    ) -> dict:
        settings = get_settings()
        if not settings.data_analyst_enabled:
            return {"status": "DISABLED", "warnings": ["DATA_ANALYST_ENABLED=false"]}
        question = str(question or "").strip()
        if not question or len(question) > 500:
            return {"status": "INVALID_QUESTION", "warnings": ["问题不能为空且不超过500字"]}

        run_id = uuid.uuid4().hex
        started = time.perf_counter()
        access_policy = (
            AnalyticsAccessPolicy.from_permissions(permissions, tenant_id=tenant_id)
            if permissions is not None
            else None
        )
        requested_page_size = page_size or _DEFAULT_ANALYTICS_PAGE_SIZE
        try:
            requested_page_size = int(requested_page_size)
        except (TypeError, ValueError):
            requested_page_size = _DEFAULT_ANALYTICS_PAGE_SIZE
        requested_page_size = max(
            1,
            min(
                requested_page_size,
                int(getattr(settings, "analytics_max_rows", 200)),
            ),
        )
        try:
            return await asyncio.wait_for(
                self._ask_within_budget(
                    question,
                    admin_id=admin_id,
                    access_policy=access_policy,
                    cursor=cursor,
                    page_size=requested_page_size,
                    run_id=run_id,
                    started=started,
                ),
                timeout=settings.analytics_request_timeout_seconds,
            )
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
                "status": "DATA_ANALYST_REQUEST_TIMEOUT",
                "warnings": ["分析超过整体超时预算，请缩小问题范围后重试"],
            }

    async def _ask_within_budget(
        self,
        question: str,
        *,
        admin_id: str,
        access_policy: AnalyticsAccessPolicy | None = None,
        cursor: str | None = None,
        page_size: int = _DEFAULT_ANALYTICS_PAGE_SIZE,
        run_id: str,
        started: float,
    ) -> dict:
        settings = get_settings()

        episode_service.start_run(
            run_id=run_id,
            message_id=None,
            user_id=f"admin:{admin_id}"[:32],
            session_id=None,
            intent="DATA_ANALYST",
            queue_name="admin.data_analyst",
            force_keep=True,
            agent_id="data_analyst",
            agent_version="v1",
            actor_type="ADMIN",
        )
        with bind_episode(run_id, message_id=None, user_id=f"admin:{admin_id}", force_keep=True):
            try:
                plan = await self._plan(question)
                episode_service.record_step(
                    "DATA_ANALYST_PLAN",
                    node_name="data_analyst_plan",
                    status="OK",
                    output_data=plan.model_dump(mode="json"),
                    agent_id="data_analyst",
                    run_id=run_id,
                )
                if plan.status == "NEEDS_CLARIFICATION":
                    episode_service.finish_run(
                        "needs_clarification",
                        run_id=run_id,
                        status="DEGRADED",
                        force_keep=True,
                    )
                    return {
                        "runId": run_id,
                        "status": "NEEDS_CLARIFICATION",
                        "clarificationQuestion": plan.clarification_question,
                        "answer": plan.clarification_question,
                        "warnings": [],
                    }
                if len(plan.branches) > 1:
                    return await self._ask_metric_tree(
                        question,
                        plan,
                        run_id=run_id,
                        started=started,
                        access_policy=access_policy,
                    )
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
                    output_data={"status": code},
                    agent_id="data_analyst",
                    run_id=run_id,
                )
                episode_service.finish_run("planning_failed", run_id=run_id, force_keep=True)
                return {"runId": run_id, "status": code, "warnings": [code]}

            sql = ""
            guard: SqlGuardResult | None = None
            explain: list[dict] = []
            warnings: list[str] = []
            feedback: str | None = None
            for attempt in range(2):
                try:
                    sql = await self._draft_sql(question, plan, feedback=feedback)
                except Exception as exc:
                    feedback = "SQL_DRAFT_FAILED"
                    episode_service.record_step(
                        "DATA_ANALYST_SQL_GUARD",
                        node_name="sql_guard",
                        status="ERROR",
                        error_code=feedback,
                        output_data={
                            "attempt": attempt + 1,
                            "reason": feedback,
                            "failureType": type(exc).__name__,
                        },
                        agent_id="data_analyst",
                        run_id=run_id,
                    )
                    continue
                guard = validate_sql(
                    sql,
                    max_days=settings.analytics_max_days,
                    max_rows=settings.analytics_max_rows,
                    expected_view=plan.semantic_view,
                    expected_start_date=plan.start_date,
                    expected_end_date=plan.end_date,
                    access_policy=access_policy,
                )
                episode_service.record_step(
                    "DATA_ANALYST_SQL_GUARD",
                    node_name="sql_guard",
                    status="OK" if guard.allowed else "BLOCKED",
                    output_data={
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
                        _explain_sql(sql, settings.analytics_query_timeout_ms),
                        timeout=settings.analytics_query_timeout_ms / 1000,
                    )
                    episode_service.record_step(
                        "DATA_ANALYST_EXPLAIN",
                        node_name="data_analyst_explain",
                        status="OK",
                        output_data={
                            "rows": explain[:10],
                            "sqlHash": hashlib.sha256(sql.encode()).hexdigest(),
                        },
                        agent_id="data_analyst",
                        run_id=run_id,
                    )
                    break
                except TimeoutError:
                    feedback = "SQL_EXPLAIN_TIMEOUT"
                    episode_service.record_step(
                        "DATA_ANALYST_EXPLAIN",
                        node_name="data_analyst_explain",
                        status="ERROR",
                        error_code=feedback,
                        output_data={
                            "attempt": attempt + 1,
                            "sqlHash": hashlib.sha256(sql.encode()).hexdigest(),
                        },
                        agent_id="data_analyst",
                        run_id=run_id,
                    )
                except RuntimeError as exc:
                    if str(exc) == "analytics DB pool not initialized":
                        episode_service.finish_run(
                            "database_unavailable", run_id=run_id, force_keep=True
                        )
                        return {
                            "runId": run_id,
                            "status": "ANALYTICS_POOL_UNAVAILABLE",
                            "sql": sql,
                            "warnings": ["ANALYTICS_POOL_UNAVAILABLE"],
                            "lineage": list(guard.tables),
                        }
                    feedback = "SQL_EXPLAIN_FAILED"
                    episode_service.record_step(
                        "DATA_ANALYST_EXPLAIN",
                        node_name="data_analyst_explain",
                        status="ERROR",
                        error_code=feedback,
                        output_data={
                            "attempt": attempt + 1,
                            "sqlHash": hashlib.sha256(sql.encode()).hexdigest(),
                        },
                        agent_id="data_analyst",
                        run_id=run_id,
                    )
                except Exception as exc:
                    if _database_error_code(exc) == 1345:
                        warning = "EXPLAIN_SKIPPED_VIEW_PRIVILEGE"
                        warnings.append(warning)
                        explain = [
                            {
                                "status": "SKIPPED",
                                "reason": "VIEW_DEFINER_PRIVILEGE_BOUNDARY",
                            }
                        ]
                        episode_service.record_step(
                            "DATA_ANALYST_EXPLAIN",
                            node_name="data_analyst_explain",
                            status="DEGRADED",
                            error_code=warning,
                            output_data={
                                "sqlHash": hashlib.sha256(sql.encode()).hexdigest(),
                                "reason": "VIEW_DEFINER_PRIVILEGE_BOUNDARY",
                            },
                            agent_id="data_analyst",
                            run_id=run_id,
                        )
                        break
                    feedback = "SQL_EXPLAIN_FAILED"
                    episode_service.record_step(
                        "DATA_ANALYST_EXPLAIN",
                        node_name="data_analyst_explain",
                        status="ERROR",
                        error_code=feedback,
                        output_data={
                            "attempt": attempt + 1,
                            "sqlHash": hashlib.sha256(sql.encode()).hexdigest(),
                        },
                        agent_id="data_analyst",
                        run_id=run_id,
                    )
            else:
                reason = feedback or "SQL_REJECTED"
                episode_service.finish_run("sql_rejected", run_id=run_id, force_keep=True)
                return {
                    "runId": run_id,
                    "status": reason,
                    "sql": sql,
                    "warnings": [reason, *warnings],
                    "lineage": list(guard.tables if guard else ()),
                }

            query_started = time.perf_counter()
            try:
                rows = _normalize_analytics_rows(
                    (
                        await asyncio.wait_for(
                            _execute_sql(sql, settings.analytics_query_timeout_ms),
                            timeout=settings.analytics_query_timeout_ms / 1000,
                        )
                    )[: settings.analytics_max_rows]
                )
            except TimeoutError:
                episode_service.record_step(
                    "DATA_ANALYST_QUERY",
                    node_name="data_analyst_query",
                    status="ERROR",
                    error_code="QUERY_TIMEOUT",
                    latency_ms=round((time.perf_counter() - query_started) * 1000),
                    agent_id="data_analyst",
                    run_id=run_id,
                )
                episode_service.finish_run("query_timeout", run_id=run_id, force_keep=True)
                return {
                    "runId": run_id,
                    "status": "QUERY_TIMEOUT",
                    "sql": sql,
                    "warnings": ["查询超过超时预算", *warnings],
                }
            except Exception:
                episode_service.record_step(
                    "DATA_ANALYST_QUERY",
                    node_name="data_analyst_query",
                    status="ERROR",
                    error_code="DATABASE_UNAVAILABLE",
                    latency_ms=round((time.perf_counter() - query_started) * 1000),
                    agent_id="data_analyst",
                    run_id=run_id,
                )
                episode_service.finish_run("database_unavailable", run_id=run_id, force_keep=True)
                return {
                    "runId": run_id,
                    "status": "DATABASE_UNAVAILABLE",
                    "sql": sql,
                    "warnings": ["分析数据库不可用", *warnings],
                }

            episode_service.record_step(
                "DATA_ANALYST_QUERY",
                node_name="data_analyst_query",
                status="OK",
                output_data={"rowCount": len(rows)},
                latency_ms=round((time.perf_counter() - query_started) * 1000),
                agent_id="data_analyst",
                run_id=run_id,
            )

            sql_hash = hashlib.sha256(sql.encode()).hexdigest()
            offset = 0
            if cursor:
                offset, cursor_error = _decode_result_cursor(
                    cursor,
                    settings=settings,
                    admin_id=admin_id,
                    sql_hash=sql_hash,
                )
                if cursor_error:
                    episode_service.record_step(
                        "DATA_ANALYST_RESULT",
                        node_name="data_analyst_result",
                        status="BLOCKED",
                        error_code=cursor_error,
                        output_data={"sqlHash": sql_hash},
                        agent_id="data_analyst",
                        run_id=run_id,
                    )
                    episode_service.finish_run(
                        "invalid_cursor", run_id=run_id, status="FAILED", force_keep=True
                    )
                    return {
                        "runId": run_id,
                        "status": cursor_error,
                        "warnings": [cursor_error],
                    }
            page_rows, byte_limited, row_too_large = _page_result_rows(
                rows,
                offset=offset or 0,
                page_size=page_size,
                max_bytes=int(getattr(settings, "analytics_max_result_bytes", 1_000_000)),
            )
            if row_too_large:
                episode_service.finish_run(
                    "result_too_large", run_id=run_id, status="FAILED", force_keep=True
                )
                return {
                    "runId": run_id,
                    "status": "RESULT_TOO_LARGE",
                    "sql": sql,
                    "warnings": ["单行结果超过 analytics_max_result_bytes"],
                }
            warnings = list(warnings)
            if byte_limited:
                warnings.append("RESULT_BYTES_TRUNCATED")
            if len(rows) >= settings.analytics_max_rows and not byte_limited:
                warnings.append("ANALYTICS_MAX_ROWS_REACHED")
            next_offset = (offset or 0) + len(page_rows)
            has_more = next_offset < len(rows)
            next_cursor = (
                _encode_result_cursor(
                    settings=settings,
                    admin_id=admin_id,
                    sql_hash=sql_hash,
                    offset=next_offset,
                )
                if has_more
                else None
            )

            columns = list(page_rows[0]) if page_rows else [*plan.dimensions, *plan.metrics]
            narrative = await self._narrative(question, plan, page_rows)
            causal_caution = _CAUSAL_CAUTION if _CAUSAL_QUESTION.search(question) else None
            answer = narrative.answer
            if causal_caution and causal_caution not in answer:
                answer = f"{answer}\n{causal_caution}"
            latency_ms = round((time.perf_counter() - started) * 1000)
            episode_service.record_step(
                "DATA_ANALYST_RESULT",
                node_name="data_analyst_result",
                status="OK",
                output_data={
                    "rowCount": len(page_rows),
                    "totalRowCount": len(rows),
                    "resultBytes": _json_size(page_rows),
                    "sqlHash": sql_hash,
                    "lineage": list(guard.tables if guard else ()),
                    "latencyMs": latency_ms,
                    "answerVersion": "v1",
                    "cursorIssued": bool(next_cursor),
                },
                agent_id="data_analyst",
                run_id=run_id,
            )
            episode_service.finish_run(
                "ok" if rows else "empty_result",
                run_id=run_id,
                status="SUCCEEDED",
                force_keep=True,
            )
            return {
                "runId": run_id,
                "answer": answer,
                "highlights": narrative.highlights,
                "sql": sql,
                "columns": columns[:20],
                "rows": page_rows,
                "chart": self._chart(columns, page_rows),
                "metricDefinitions": _metric_definitions(plan),
                "interpretation": plan.interpretation,
                "lineage": list(guard.tables if guard else ()),
                "warnings": list(dict.fromkeys(warnings)),
                "status": "SUCCEEDED" if page_rows else "EMPTY_RESULT",
                "explain": explain[:10],
                "causalCaution": causal_caution,
                "latencyMs": latency_ms,
                "nextCursor": next_cursor,
                "page": {
                    "offset": offset or 0,
                    "size": len(page_rows),
                    "hasMore": bool(next_cursor),
                    "maxRows": settings.analytics_max_rows,
                },
            }


data_analyst_service = DataAnalystService()
