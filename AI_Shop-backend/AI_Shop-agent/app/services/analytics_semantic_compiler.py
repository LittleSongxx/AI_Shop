from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.services.analytics_catalog import allowed_columns, column_contract, view_contract

SUPPLY_CHAIN_COMPILER_VIEWS = frozenset(
    {"analytics_inventory_forecast", "analytics_inventory_risk"}
)


class SemanticFilter(BaseModel):
    column: str = Field(min_length=1, max_length=64)
    operator: Literal["EQ", "LT", "LTE", "GT", "GTE", "BETWEEN", "IS_NULL", "IS_NOT_NULL"]
    value: str | int | float | None = None
    second_value: str | int | float | None = None

    @model_validator(mode="after")
    def validate_operands(self) -> "SemanticFilter":
        if self.operator in {"IS_NULL", "IS_NOT_NULL"}:
            if self.value is not None or self.second_value is not None:
                raise ValueError("null filters do not accept values")
        elif self.operator == "BETWEEN":
            if self.value is None or self.second_value is None:
                raise ValueError("BETWEEN requires two values")
        elif self.value is None or self.second_value is not None:
            raise ValueError("comparison filters require exactly one value")
        return self


class SemanticOrder(BaseModel):
    column: str = Field(min_length=1, max_length=64)
    direction: Literal["ASC", "DESC"] = "ASC"


class SemanticPlanUnsupported(ValueError):
    pass


def _unsupported() -> SemanticPlanUnsupported:
    return SemanticPlanUnsupported("SEMANTIC_PLAN_UNSUPPORTED")


def _literal(view: str, column: str, value: str | int | float) -> str:
    contract = column_contract(view, column)
    kind = str(contract.get("type") or "").upper()
    if isinstance(value, bool):
        raise _unsupported()
    if kind in {"INTEGER", "DECIMAL"}:
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise _unsupported() from exc
        if not number.is_finite() or (kind == "INTEGER" and number != number.to_integral_value()):
            raise _unsupported()
        return format(number, "f")
    if kind == "DATE":
        try:
            parsed = date.fromisoformat(str(value))
        except ValueError as exc:
            raise _unsupported() from exc
        return f"'{parsed.isoformat()}'"
    text = str(value)
    if (
        len(text) > 200
        or "\\" in text
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
    ):
        raise _unsupported()
    return "'" + text.replace("'", "''") + "'"


def _filter_sql(view: str, item: SemanticFilter) -> str:
    contract = column_contract(view, item.column)
    if not contract or item.column not in allowed_columns(view):
        raise _unsupported()
    if item.operator in {"IS_NULL", "IS_NOT_NULL"}:
        if item.operator == "IS_NULL" and contract.get("nullable") is False:
            raise _unsupported()
        return f"{item.column} IS {'NULL' if item.operator == 'IS_NULL' else 'NOT NULL'}"
    if item.value is None:
        raise _unsupported()
    if item.operator == "BETWEEN":
        if item.second_value is None:
            raise _unsupported()
        return (
            f"{item.column} BETWEEN {_literal(view, item.column, item.value)} "
            f"AND {_literal(view, item.column, item.second_value)}"
        )
    operator = {"EQ": "=", "LT": "<", "LTE": "<=", "GT": ">", "GTE": ">="}[item.operator]
    return f"{item.column} {operator} {_literal(view, item.column, item.value)}"


def compile_supply_chain_sql(
    *,
    view: str,
    metrics: list[str],
    dimensions: list[str],
    start_date: date | None,
    end_date: date | None,
    filters: list[SemanticFilter],
    order_by: list[SemanticOrder],
    top_k: int,
) -> str | None:
    """Compile validated inventory plans; return None for the other eight views."""
    if view not in SUPPLY_CHAIN_COMPILER_VIEWS:
        return None
    if start_date is None or end_date is None or end_date < start_date or not 1 <= top_k <= 200:
        raise _unsupported()

    contract = view_contract(view)
    date_column = str(contract.get("dateColumn") or "")
    physical_columns = allowed_columns(view)
    selected_dimensions = list(dict.fromkeys(dimensions))
    selected_metrics = list(dict.fromkeys(metrics))
    if (
        not date_column
        or not selected_metrics
        or not set(selected_dimensions).issubset(physical_columns)
        or any(
            str(column_contract(view, name).get("aggregation") or "") != "DIMENSION"
            for name in selected_dimensions
        )
    ):
        raise _unsupported()

    risk_level_count = (
        view == "analytics_inventory_risk"
        and selected_dimensions == ["risk_level"]
        and selected_metrics == ["stockout_sku_count"]
    )
    projections = list(selected_dimensions)
    group_by: list[str] = []
    if risk_level_count:
        projections.append("COUNT(*) AS sku_count")
        group_by = selected_dimensions
    else:
        grain = set(str(item) for item in contract.get("grain") or ()) - {date_column}
        detail_query = grain.issubset(selected_dimensions)
        for metric in selected_metrics:
            metric_contract = column_contract(view, metric)
            if not metric_contract or metric_contract.get("aggregation") == "DIMENSION":
                raise _unsupported()
            expression = metric_contract.get("sqlExpression")
            if detail_query:
                if expression:
                    raise _unsupported()
                projections.append(metric)
                continue
            if expression:
                projections.append(f"{expression} AS {metric}")
            elif metric_contract.get("aggregation") in {"SUM", "SNAPSHOT_SUM"}:
                projections.append(f"SUM({metric}) AS {metric}")
            else:
                raise _unsupported()
        if (
            detail_query
            and view == "analytics_inventory_risk"
            and "stock" in projections
            and "risk_level" in projections
        ):
            projections.remove("stock")
            projections.insert(projections.index("risk_level"), "stock")
        group_by = [] if detail_query else selected_dimensions

    if not projections or len(projections) > 20:
        raise _unsupported()
    predicates = [f"{date_column} BETWEEN '{start_date.isoformat()}' AND '{end_date.isoformat()}'"]
    for item in filters:
        if item.column == date_column:
            raise _unsupported()
        predicates.append(_filter_sql(view, item))

    selectable = {*selected_dimensions, *selected_metrics}
    if risk_level_count:
        selectable.add("sku_count")
    orders: list[tuple[str, str]] = []
    for item in order_by:
        if item.column not in selectable:
            raise _unsupported()
        orders.append((item.column, item.direction))
    stable_keys = (
        ("product_id", "property_value_id_hash")
        if view == "analytics_inventory_risk"
        else ("product_id", "sku_key")
    )
    if not orders and selected_dimensions == ["risk_level"]:
        orders.append(("risk_level", "ASC"))
    for column in stable_keys:
        if column in selectable and column not in {name for name, _ in orders}:
            orders.append((column, "ASC"))

    sql = f"SELECT {', '.join(projections)} FROM {view} WHERE {' AND '.join(predicates)}"
    if group_by:
        sql += f" GROUP BY {', '.join(group_by)}"
    if orders:
        sql += " ORDER BY " + ", ".join(f"{column} {direction}" for column, direction in orders)
    return f"{sql} LIMIT {top_k}"
