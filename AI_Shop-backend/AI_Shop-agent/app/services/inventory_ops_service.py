"""Governed, SKU-level inventory planning suggestions.

InventoryOps is intentionally a copilot: it reads one PII-free semantic view,
persists an immutable-ish forecast snapshot for traceability, and never writes
stock, supplier, purchase-order, or other business state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
import uuid
from datetime import date
from numbers import Number
from typing import Any

from app.config.settings import get_settings
from app.db.pool import acquire
from app.services.data_analyst_service import _execute_sql
from app.services.episode_service import bind_episode, episode_service
from app.services.sql_guard import validate_sql

FORECAST_VIEW = "analytics_inventory_forecast"
FORECAST_LINEAGE = [FORECAST_VIEW, "analytics_inventory_risk"]


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, Number) and not isinstance(value, bool):
        return float(value)
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def calculate_inventory_forecast(values: dict[str, Any]) -> dict[str, float | int | None]:
    """Calculate the governed ROP, review-period gap and MOQ-rounded suggestion."""

    def value(*names: str, default: float = 0.0) -> float:
        for name in names:
            if name in values and values[name] is not None:
                return _number(values[name], default)
        return default

    demand = max(0.0, value("ewma_daily_demand", "ewmaDailyDemand"))
    lead_time = max(0.0, value("lead_time_days", "leadTimeDays", default=7.0))
    safety_stock = max(0.0, value("safety_stock", "safetyStock"))
    review_period = max(
        0.0, value("review_period_days", "reviewPeriodDays", default=14.0)
    )
    minimum_order = max(
        1, int(value("min_order_quantity", "minOrderQuantity", default=1.0))
    )
    current_stock = max(0.0, value("current_stock", "currentStock"))
    inbound = max(0.0, value("inbound_quantity", "inboundQuantity"))

    reorder_point = round(demand * lead_time + safety_stock, 2)
    gap = max(
        0.0,
        demand * (lead_time + review_period)
        + safety_stock
        - current_stock
        - inbound,
    )
    suggested = int(math.ceil(gap / minimum_order) * minimum_order) if gap else 0
    coverage = None if demand <= 0 else round((current_stock + inbound) / demand, 2)
    return {
        "reorderPoint": reorder_point,
        "suggestedReplenishQuantity": suggested,
        "coverageDays": coverage,
    }


class InventoryOpsService:
    @staticmethod
    def _priority(row: dict[str, Any]) -> tuple[str, int, str]:
        """Rank human follow-up urgency from deterministic inventory facts."""
        stock = _number(row.get("current_stock"))
        demand = _number(row.get("ewma_daily_demand"))
        reorder_point = _number(row.get("reorder_point"))
        replenish = _number(row.get("suggested_replenish_quantity"))
        risk_level = str(row.get("risk_level") or "NORMAL")
        confidence = _number(row.get("confidence"))

        if replenish > 0 and stock <= 0 and demand > 0:
            return (
                "CRITICAL",
                100 + min(round(demand * 10), 50),
                "当前缺货且近 28 天存在净需求，优先核查供应与人工补货。",
            )
        if replenish > 0 and (stock <= 0 or stock <= reorder_point):
            score = 80 + min(round(demand * 10), 15)
            if confidence < 0.25:
                return (
                    "HIGH",
                    score,
                    "库存低于再订货点，但需求置信度偏低，先人工复核后再决定补货。",
                )
            return (
                "HIGH",
                score,
                "库存低于再订货点，按交期、安全库存和 MOQ 生成补货待办。",
            )
        if risk_level == "LOW_STOCK" and demand > 0:
            return "MEDIUM", 55 + min(round(demand * 5), 20), "低库存且有需求信号，加入人工观察待办。"
        if risk_level == "OUT_OF_STOCK":
            return "MEDIUM", 50, "当前缺货但没有可靠需求信号，先核查商品经营状态。"
        return "LOW", 30, "当前无需补货，保留预测供后续复核。"

    @staticmethod
    def _forecast_id(row: dict[str, Any], snapshot_date: str) -> str:
        key = f"{snapshot_date}:{row.get('product_id')}:{row.get('sku_key')}"
        return "forecast_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:55]

    async def _persist_forecasts(
        self,
        rows: list[dict[str, Any]],
        *,
        snapshot_date: str,
    ) -> tuple[int, str | None]:
        if not rows:
            return 0, None
        try:
            async with acquire() as cur:
                for row in rows:
                    forecast_id = self._forecast_id(row, snapshot_date)
                    payload = {
                        "snapshotDate": snapshot_date,
                        "productId": row.get("product_id"),
                        "productName": row.get("product_name"),
                        "skuKey": row.get("sku_key"),
                        "currentStock": row.get("current_stock"),
                        "inboundQuantity": row.get("inbound_quantity"),
                        "ewmaDailyDemand": row.get("ewma_daily_demand"),
                        "leadTimeDays": row.get("lead_time_days"),
                        "safetyStock": row.get("safety_stock"),
                        "reviewPeriodDays": row.get("review_period_days"),
                        "minOrderQuantity": row.get("min_order_quantity"),
                        "reorderPoint": row.get("reorder_point"),
                        "suggestedReplenishQuantity": row.get(
                            "suggested_replenish_quantity"
                        ),
                        "coverageDays": row.get("coverage_days"),
                        "confidence": row.get("confidence"),
                        "riskLevel": row.get("risk_level"),
                        "priority": row.get("priority"),
                        "priorityScore": row.get("priority_score"),
                        "manualOnly": True,
                    }
                    await cur.execute(
                        """
                        INSERT INTO agent_inventory_forecast
                            (forecast_id, product_id, sku_key, forecast_json,
                             status, generated_at)
                        VALUES (%s, %s, %s, %s, 'OPEN', NOW(3))
                        ON DUPLICATE KEY UPDATE
                            forecast_json = %s,
                            status = 'OPEN',
                            generated_at = NOW(3),
                            reviewed_by = NULL,
                            reviewed_at = NULL
                        """,
                        (
                            forecast_id,
                            str(row.get("product_id") or ""),
                            str(row.get("sku_key") or ""),
                            json.dumps(payload, ensure_ascii=False, default=str),
                            json.dumps(payload, ensure_ascii=False, default=str),
                        ),
                    )
            return len(rows), None
        except Exception as exc:
            return 0, type(exc).__name__

    async def suggestions(
        self,
        *,
        admin_id: str,
        lookback_days: int = 28,
        limit: int = 50,
    ) -> dict:
        settings = get_settings()
        if not getattr(settings, "inventory_ops_enabled", True):
            return {"status": "DISABLED", "warnings": ["INVENTORY_OPS_ENABLED=false"]}
        if not settings.data_analyst_enabled:
            return {"status": "DISABLED", "warnings": ["DATA_ANALYST_ENABLED=false"]}

        # The semantic view deliberately owns the fixed 28-day EWMA contract.
        requested_lookback = max(1, int(lookback_days))
        lookback_days = 28
        limit = min(100, max(1, int(limit)))
        settings_rows = min(settings.analytics_max_rows, max(limit, 50))
        query = (
            "SELECT snapshot_date, product_id, product_name, sku_key, risk_level, "
            "current_stock, inbound_quantity, ewma_daily_demand, lead_time_days, "
            "safety_stock, review_period_days, min_order_quantity, reorder_point, "
            "suggested_replenish_quantity, coverage_days, confidence "
            f"FROM {FORECAST_VIEW} "
            "ORDER BY suggested_replenish_quantity DESC, current_stock ASC, product_id ASC "
            f"LIMIT {settings_rows}"
        )
        guard = validate_sql(
            query,
            max_rows=settings.analytics_max_rows,
            expected_view=FORECAST_VIEW,
        )
        if not guard.allowed:
            return {"status": "INTERNAL_POLICY_ERROR", "warnings": ["INVENTORY_SQL_GUARD_FAILED"]}

        run_id = uuid.uuid4().hex
        started = time.perf_counter()
        episode_service.start_run(
            run_id=run_id,
            message_id=None,
            user_id=f"admin:{admin_id}"[:32],
            session_id=None,
            intent="INVENTORY_OPS",
            queue_name="admin.inventory_ops",
            force_keep=True,
            agent_id="inventory_ops",
            agent_version="v2",
            actor_type="ADMIN",
        )
        with bind_episode(run_id, message_id=None, user_id=f"admin:{admin_id}", force_keep=True):
            episode_service.record_step(
                "INVENTORY_OPS_PLAN",
                node_name="inventory_ops_plan",
                output_data={
                    "requestedLookbackDays": requested_lookback,
                    "lookbackDays": lookback_days,
                    "limit": limit,
                    "lineage": FORECAST_LINEAGE,
                    "formula": "ROP = EWMA 日需求 × 交期 + 安全库存；建议量按 MOQ 向上取整",
                    "reviewPeriodDays": 14,
                    "manualOnly": True,
                },
                agent_id="inventory_ops",
                run_id=run_id,
            )
            try:
                rows = (
                    await asyncio.wait_for(
                        _execute_sql(guard.sql, settings.analytics_query_timeout_ms),
                        timeout=settings.analytics_query_timeout_ms / 1000,
                    )
                )[: settings_rows]
            except TimeoutError:
                episode_service.finish_run("query_timeout", run_id=run_id, status="FAILED", force_keep=True)
                return {"runId": run_id, "status": "QUERY_TIMEOUT", "warnings": ["查询超过超时预算"]}
            except Exception:
                episode_service.finish_run(
                    "database_unavailable", run_id=run_id, status="FAILED", force_keep=True
                )
                return {"runId": run_id, "status": "DATABASE_UNAVAILABLE", "warnings": ["分析数据库不可用"]}

            normalized: list[dict[str, Any]] = []
            snapshot_date = str(date.today())
            for raw in rows:
                row = dict(raw)
                calculated = calculate_inventory_forecast(row)
                row["reorder_point"] = calculated["reorderPoint"]
                row["suggested_replenish_quantity"] = calculated[
                    "suggestedReplenishQuantity"
                ]
                row["coverage_days"] = calculated["coverageDays"]
                priority, score, action = self._priority(row)
                row["priority"], row["priority_score"], row["suggested_action"] = priority, score, action
                normalized.append(row)
            normalized.sort(
                key=lambda row: (
                    -int(row.get("priority_score") or 0),
                    -_number(row.get("suggested_replenish_quantity")),
                    _number(row.get("current_stock")),
                    str(row.get("product_id") or ""),
                    str(row.get("sku_key") or ""),
                )
            )
            selected = normalized[:limit]
            persisted_count, persist_error = await self._persist_forecasts(
                selected,
                snapshot_date=snapshot_date,
            )
            warnings: list[str] = []
            if requested_lookback != lookback_days:
                warnings.append("INVENTORY_LOOKBACK_FIXED_TO_28_DAYS")
            if persist_error:
                warnings.append("INVENTORY_FORECAST_PERSIST_DEGRADED")

            suggestions = [
                {
                    "productId": str(row.get("product_id") or ""),
                    "productName": row.get("product_name"),
                    "skuHash": row.get("sku_key"),
                    "stock": int(_number(row.get("current_stock"))),
                    "riskLevel": row.get("risk_level"),
                    "inboundQuantity": _number(row.get("inbound_quantity")),
                    "ewmaDailyDemand": _number(row.get("ewma_daily_demand")),
                    "leadTimeDays": int(_number(row.get("lead_time_days"), 7)),
                    "safetyStock": _number(row.get("safety_stock")),
                    "reviewPeriodDays": int(_number(row.get("review_period_days"), 14)),
                    "minOrderQuantity": int(_number(row.get("min_order_quantity"), 1)),
                    "reorderPoint": _number(row.get("reorder_point")),
                    "suggestedReplenishQuantity": int(
                        _number(row.get("suggested_replenish_quantity"))
                    ),
                    "coverageDays": row.get("coverage_days"),
                    "confidence": _number(row.get("confidence")),
                    "priority": row["priority"],
                    "priorityScore": row["priority_score"],
                    "suggestedAction": row["suggested_action"],
                    "manualOnly": True,
                }
                for row in selected
            ]
            latency_ms = round((time.perf_counter() - started) * 1000)
            status = "DEGRADED" if persist_error else "SUCCEEDED"
            replenishment_count = sum(
                item["suggestedReplenishQuantity"] > 0 for item in suggestions
            )
            suggested_total = sum(
                item["suggestedReplenishQuantity"] for item in suggestions
            )
            average_ewma = (
                round(
                    sum(item["ewmaDailyDemand"] for item in suggestions)
                    / len(suggestions),
                    4,
                )
                if suggestions
                else 0.0
            )
            episode_service.record_step(
                "INVENTORY_OPS_RESULT",
                node_name="inventory_ops_result",
                status="DEGRADED" if persist_error else "OK",
                error_code="INVENTORY_FORECAST_PERSIST_DEGRADED" if persist_error else None,
                output_data={
                    "suggestionCount": len(suggestions),
                    "persistedForecastCount": persisted_count,
                    "replenishmentCount": replenishment_count,
                    "suggestedReplenishQuantity": suggested_total,
                    "criticalCount": sum(item["priority"] == "CRITICAL" for item in suggestions),
                    "averageEwmaDailyDemand": average_ewma,
                    "moqGovernedCount": sum(
                        item["suggestedReplenishQuantity"] > 0
                        and item["suggestedReplenishQuantity"]
                        % max(1, item["minOrderQuantity"])
                        == 0
                        for item in suggestions
                    ),
                    "persistErrorType": persist_error,
                    "queryHash": hashlib.sha256(guard.sql.encode()).hexdigest(),
                    "lineage": FORECAST_LINEAGE,
                    "latencyMs": latency_ms,
                    "manualOnly": True,
                },
                latency_ms=latency_ms,
                agent_id="inventory_ops",
                run_id=run_id,
            )
            episode_service.finish_run(
                "ok" if not persist_error else "forecast_persist_degraded",
                run_id=run_id,
                status=status,
                latency_ms=latency_ms,
                force_keep=True,
            )
            return {
                "runId": run_id,
                "status": status,
                "manualOnly": True,
                "lookbackDays": lookback_days,
                "reviewPeriodDays": 14,
                "suggestions": suggestions,
                "summary": {
                    "riskSkuCount": len(suggestions),
                    "replenishmentSkuCount": replenishment_count,
                    "suggestedReplenishQuantity": suggested_total,
                    "criticalCount": sum(item["priority"] == "CRITICAL" for item in suggestions),
                    "highCount": sum(item["priority"] == "HIGH" for item in suggestions),
                },
                "lineage": FORECAST_LINEAGE,
                "metricDefinitions": [
                    {
                        "name": "ewmaDailyDemand",
                        "definition": "最近 28 个自然日的净支付需求，按 0.90 衰减并补齐零销量日。",
                    },
                    {
                        "name": "reorderPoint",
                        "definition": "ROP = EWMA 日需求 × 供应交期 + 安全库存。",
                    },
                    {
                        "name": "suggestedReplenishQuantity",
                        "definition": "覆盖交期与 14 天复核周期后的缺口，按 SKU MOQ 向上取整；仅供人工审批。",
                    },
                    {
                        "name": "confidence",
                        "definition": "最近 28 天存在有效净需求的天数 / 28，不代表因果或供应确定性。",
                    },
                ],
                "warnings": warnings,
                "latencyMs": latency_ms,
            }


inventory_ops_service = InventoryOpsService()
