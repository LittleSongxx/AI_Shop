from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from datetime import date, timedelta

from app.config.settings import get_settings
from app.services.data_analyst_service import _execute_sql
from app.services.episode_service import bind_episode, episode_service
from app.services.sql_guard import validate_sql


class InventoryOpsService:
    @staticmethod
    def _priority(risk_level: str, net_units: int) -> tuple[str, int, str]:
        if risk_level == "OUT_OF_STOCK" and net_units > 0:
            return "CRITICAL", 100 + min(net_units, 50), "有近期销量但当前缺货，优先核查供应与补货。"
        if risk_level == "OUT_OF_STOCK":
            return "HIGH", 80, "当前缺货但近期无销量，先核查商品状态与备货必要性。"
        if net_units >= 10:
            return "HIGH", 60 + min(net_units, 30), "库存偏低且近期需求较高，安排人工补货评估。"
        if net_units > 0:
            return "MEDIUM", 45 + min(net_units, 15), "库存偏低且存在近期销量，加入补货待办。"
        return "LOW", 30, "库存偏低但近期无销量，核查是否继续经营该 SKU。"

    async def suggestions(
        self,
        *,
        admin_id: str,
        lookback_days: int = 30,
        limit: int = 50,
    ) -> dict:
        settings = get_settings()
        if not settings.data_analyst_enabled:
            return {"status": "DISABLED", "warnings": ["DATA_ANALYST_ENABLED=false"]}
        lookback_days = min(settings.analytics_max_days, max(7, int(lookback_days)))
        limit = min(100, max(1, int(limit)))
        end = date.today()
        start = end - timedelta(days=lookback_days - 1)
        inventory_sql = (
            "SELECT product_id, product_name, property_value_id_hash, stock, risk_level "
            "FROM analytics_inventory_risk "
            "WHERE risk_level IN ('OUT_OF_STOCK', 'LOW_STOCK') "
            f"ORDER BY stock LIMIT {settings.analytics_max_rows}"
        )
        sales_sql = (
            "SELECT product_id, SUM(paid_units) AS paid_units, "
            "SUM(refunded_units) AS refunded_units "
            "FROM analytics_product_sales_daily "
            f"WHERE date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}' "
            "GROUP BY product_id ORDER BY paid_units DESC "
            f"LIMIT {settings.analytics_max_rows}"
        )
        inventory_guard = validate_sql(
            inventory_sql,
            max_rows=settings.analytics_max_rows,
            expected_view="analytics_inventory_risk",
        )
        sales_guard = validate_sql(
            sales_sql,
            max_days=settings.analytics_max_days,
            max_rows=settings.analytics_max_rows,
            expected_view="analytics_product_sales_daily",
            expected_start_date=start,
            expected_end_date=end,
        )
        if not inventory_guard.allowed or not sales_guard.allowed:
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
            agent_version="v1",
            actor_type="ADMIN",
        )
        with bind_episode(run_id, message_id=None, user_id=f"admin:{admin_id}", force_keep=True):
            episode_service.record_step(
                "INVENTORY_OPS_PLAN",
                node_name="inventory_ops_plan",
                output_data={
                    "lookbackDays": lookback_days,
                    "limit": limit,
                    "lineage": ["analytics_inventory_risk", "analytics_product_sales_daily"],
                    "manualOnly": True,
                },
                agent_id="inventory_ops",
                run_id=run_id,
            )
            try:
                inventory_rows, sales_rows = await asyncio.wait_for(
                    asyncio.gather(
                        _execute_sql(inventory_guard.sql, settings.analytics_query_timeout_ms),
                        _execute_sql(sales_guard.sql, settings.analytics_query_timeout_ms),
                    ),
                    timeout=settings.analytics_query_timeout_ms / 1000,
                )
            except TimeoutError:
                episode_service.finish_run("query_timeout", run_id=run_id, force_keep=True)
                return {"runId": run_id, "status": "QUERY_TIMEOUT", "warnings": ["查询超过超时预算"]}
            except Exception:
                episode_service.finish_run("database_unavailable", run_id=run_id, force_keep=True)
                return {"runId": run_id, "status": "DATABASE_UNAVAILABLE", "warnings": ["分析数据库不可用"]}

            sales_by_product = {}
            for row in sales_rows:
                paid = int(row.get("paid_units") or 0)
                refunded = int(row.get("refunded_units") or 0)
                sales_by_product[str(row.get("product_id") or "")] = max(0, paid - refunded)
            suggestions = []
            for row in inventory_rows:
                product_id = str(row.get("product_id") or "")
                net_units = sales_by_product.get(product_id, 0)
                priority, score, action = self._priority(str(row.get("risk_level") or ""), net_units)
                suggestions.append(
                    {
                        "productId": product_id,
                        "productName": row.get("product_name"),
                        "skuHash": row.get("property_value_id_hash"),
                        "stock": int(row.get("stock") or 0),
                        "riskLevel": row.get("risk_level"),
                        "productNetUnits": net_units,
                        "lookbackDays": lookback_days,
                        "priority": priority,
                        "priorityScore": score,
                        "suggestedAction": action,
                        "manualOnly": True,
                    }
                )
            suggestions.sort(key=lambda item: (-item["priorityScore"], item["stock"], item["productId"]))
            suggestions = suggestions[:limit]
            latency_ms = round((time.perf_counter() - started) * 1000)
            episode_service.record_step(
                "INVENTORY_OPS_RESULT",
                node_name="inventory_ops_result",
                output_data={
                    "suggestionCount": len(suggestions),
                    "criticalCount": sum(item["priority"] == "CRITICAL" for item in suggestions),
                    "inventorySqlHash": hashlib.sha256(inventory_guard.sql.encode()).hexdigest(),
                    "salesSqlHash": hashlib.sha256(sales_guard.sql.encode()).hexdigest(),
                    "latencyMs": latency_ms,
                    "manualOnly": True,
                },
                latency_ms=latency_ms,
                agent_id="inventory_ops",
                run_id=run_id,
            )
            episode_service.finish_run("ok", run_id=run_id, latency_ms=latency_ms, force_keep=True)
            return {
                "runId": run_id,
                "status": "SUCCEEDED",
                "manualOnly": True,
                "lookbackDays": lookback_days,
                "suggestions": suggestions,
                "summary": {
                    "riskSkuCount": len(suggestions),
                    "criticalCount": sum(item["priority"] == "CRITICAL" for item in suggestions),
                    "highCount": sum(item["priority"] == "HIGH" for item in suggestions),
                },
                "lineage": ["analytics_inventory_risk", "analytics_product_sales_daily"],
                "metricDefinitions": [
                    {"name": "productNetUnits", "definition": f"最近 {lookback_days} 天商品支付件数减已完成退款件数；该信号按商品聚合，不推断单 SKU 销速。"},
                    {"name": "priorityScore", "definition": "由缺货/低库存状态与商品级近期净销量形成的排序分，仅用于人工待办。"},
                ],
                "warnings": [],
                "latencyMs": latency_ms,
            }


inventory_ops_service = InventoryOpsService()
