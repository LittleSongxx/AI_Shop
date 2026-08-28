from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from app.services.analytics_catalog import CATALOG
from evaluation.text2sql import CATALOG_SCHEMA_VERSION
from evaluation.text2sql.io import canonical_json_bytes, sha256_bytes, sha256_file, write_json

CATALOG_VERSION = "analytics-provisional-v0.20260827"
FIXED_TIMEZONE = "Asia/Shanghai"
DEFAULT_CURRENCY = "CNY"
ADMIN_DDL = (
    Path(__file__).resolve().parents[3]
    / "AI_Shop-admin"
    / "src"
    / "main"
    / "resources"
    / "db"
    / "migration"
    / "R__current_schema.sql"
)


def _column(
    kind: str,
    *,
    unit: str | None = None,
    scale: int | None = None,
    nullable: bool = False,
    additive: bool = False,
    aggregation: str = "DIMENSION",
    display_scale: int | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": kind,
        "nullable": nullable,
        "additive": additive,
        "aggregation": aggregation,
    }
    if unit is not None:
        value["unit"] = unit
    if scale is not None:
        value["scale"] = scale
    if display_scale is not None:
        value["displayScale"] = display_scale
    return value


DATE = _column("DATE", unit="CALENDAR_DATE")
STRING = _column("STRING")
ENUM = _column("ENUM")
COUNT = _column("INTEGER", unit="COUNT", additive=True, aggregation="SUM")
UNITS = _column("INTEGER", unit="ITEM", additive=True, aggregation="SUM")
STOCK = _column("INTEGER", unit="ITEM", additive=True, aggregation="SNAPSHOT_SUM")
MONEY = _column(
    "DECIMAL", unit=DEFAULT_CURRENCY, scale=2, additive=True, aggregation="SUM", display_scale=2
)
MONEY_AVG = _column(
    "DECIMAL", unit=DEFAULT_CURRENCY, scale=2, aggregation="WEIGHTED_AVG", display_scale=2
)
RATE = _column("DECIMAL", unit="RATIO", scale=4, aggregation="NON_ADDITIVE")
LATENCY = _column("DECIMAL", unit="MILLISECOND", scale=4, aggregation="WEIGHTED_AVG")


COLUMN_SPECS: dict[str, dict[str, dict[str, Any]]] = {
    "analytics_sales_daily": {
        "date": DATE,
        "paid_order_count": COUNT,
        "gross_paid_amount": MONEY,
        "completed_refund_count": COUNT,
        "completed_refund_amount": MONEY,
        "net_paid_amount": MONEY,
    },
    "analytics_product_sales_daily": {
        "date": DATE,
        "product_id": STRING,
        "product_name": STRING,
        "paid_units": UNITS,
        "gross_item_amount": MONEY,
        "refunded_units": UNITS,
    },
    "analytics_inventory_risk": {
        "snapshot_date": DATE,
        "product_id": STRING,
        "product_name": STRING,
        "property_value_id_hash": STRING,
        "stock": STOCK,
        "risk_level": ENUM,
        "stockout_sku_count": COUNT,
    },
    "analytics_agent_quality_daily": {
        "date": DATE,
        "agent_id": STRING,
        "intent": ENUM,
        "run_count": COUNT,
        "success_count": COUNT,
        "failure_count": COUNT,
        "human_handoff_count": COUNT,
        "avg_latency_ms": LATENCY,
        "input_tokens": _column("INTEGER", unit="TOKEN", additive=True, aggregation="SUM"),
        "output_tokens": _column("INTEGER", unit="TOKEN", additive=True, aggregation="SUM"),
        "cost_cny": _column(
            "DECIMAL",
            unit=DEFAULT_CURRENCY,
            scale=8,
            additive=True,
            aggregation="SUM",
            display_scale=2,
        ),
    },
    "analytics_tool_quality_daily": {
        "date": DATE,
        "agent_id": STRING,
        "tool_name": STRING,
        "call_count": COUNT,
        "success_count": COUNT,
        "failure_count": COUNT,
        "avg_latency_ms": LATENCY,
    },
    "analytics_recommendation_funnel_daily": {
        "date": DATE,
        "retrieval_mode": ENUM,
        "impression_count": COUNT,
        "click_count": COUNT,
        "add_to_cart_count": COUNT,
        "payment_count": COUNT,
        "click_through_rate": RATE,
        "cart_rate": RATE,
        "payment_rate": RATE,
    },
    "analytics_recommendation_quality_daily": {
        "date": DATE,
        "product_id": STRING,
        "payment_count": COUNT,
        "refund_count": COUNT,
        "return_count": COUNT,
        "negative_review_count": COUNT,
        "support_contact_count": COUNT,
        "repeat_purchase_count": COUNT,
    },
    "analytics_offer_quality_daily": {
        "date": DATE,
        "product_id": STRING,
        "quote_count": COUNT,
        "coupon_available_count": COUNT,
        "avg_base_price": MONEY_AVG,
        "avg_estimated_payable": MONEY_AVG,
        "in_stock_quote_count": COUNT,
    },
    "analytics_fulfillment_after_sales_daily": {
        "date": DATE,
        "paid_order_count": COUNT,
        "shipped_order_count": COUNT,
        "completed_order_count": COUNT,
        "cancelled_order_count": COUNT,
        "refund_request_count": COUNT,
        "refund_completed_count": COUNT,
        "refund_completed_amount": MONEY,
    },
    "analytics_inventory_forecast": {
        "snapshot_date": DATE,
        "product_id": STRING,
        "product_name": STRING,
        "sku_key": STRING,
        "risk_level": ENUM,
        "current_stock": STOCK,
        "inbound_quantity": STOCK,
        "ewma_daily_demand": _column(
            "DECIMAL", unit="ITEM_PER_DAY", scale=4, aggregation="NON_ADDITIVE"
        ),
        "lead_time_days": _column("INTEGER", unit="DAY", aggregation="SNAPSHOT"),
        "safety_stock": STOCK,
        "review_period_days": _column("INTEGER", unit="DAY", aggregation="SNAPSHOT"),
        "min_order_quantity": UNITS,
        "reorder_point": _column(
            "DECIMAL", unit="ITEM", scale=2, aggregation="NON_ADDITIVE"
        ),
        "suggested_replenish_quantity": UNITS,
        "coverage_days": _column(
            "DECIMAL", unit="DAY", scale=2, nullable=True, aggregation="NON_ADDITIVE"
        ),
        "confidence": RATE,
    },
}


VIEW_POLICIES: dict[str, dict[str, Any]] = {
    "analytics_sales_daily": {
        "grain": ["date"],
        "dateOwnership": "订单金额归订单创建日；退款金额归退款完成日。",
        "answerability": "PROVISIONAL_WITH_DISCLOSURE",
        "mustDisclose": ["期间", "dataAsOf", "catalogVersion", "暂定口径/仅供运营核对"],
        "forbiddenClaims": ["结算收入", "审计收入", "财务已确认收入"],
    },
    "analytics_product_sales_daily": {
        "grain": ["date", "product_id"],
        "dateOwnership": "购买件数和行金额归订单创建日；退款件数归退款完成日。",
        "answerability": "PROVISIONAL_WITH_DISCLOSURE",
        "mustDisclose": ["商品名为下单快照", "期间", "dataAsOf"],
        "forbiddenClaims": ["商品净销售额", "结算销量"],
    },
    "analytics_inventory_risk": {
        "grain": ["snapshot_date", "product_id", "property_value_id_hash"],
        "dateOwnership": "CURRENT_DATE 的当前可用库存快照，不保留历史快照。",
        "answerability": "CURRENT_SNAPSHOT_ONLY",
        "mustDisclose": ["当前快照", "dataAsOf"],
        "forbiddenClaims": ["历史库存", "滞销", "未来缺货概率"],
    },
    "analytics_agent_quality_daily": {
        "grain": ["date", "agent_id", "intent"],
        "dateOwnership": "Agent run.started_at 所在日期。",
        "answerability": "PROVISIONAL_WITH_DISCLOSURE",
        "mustDisclose": ["期间", "dataAsOf"],
        "forbiddenClaims": ["用户满意度", "业务成功率", "线上 SLA"],
    },
    "analytics_tool_quality_daily": {
        "grain": ["date", "agent_id", "tool_name"],
        "dateOwnership": "所属 Agent run.started_at 所在日期。",
        "answerability": "PROVISIONAL_WITH_DISCLOSURE",
        "mustDisclose": ["技术调用状态口径", "期间", "dataAsOf"],
        "forbiddenClaims": ["业务成功率", "工具正确率"],
    },
    "analytics_recommendation_funnel_daily": {
        "grain": ["date", "retrieval_mode"],
        "dateOwnership": "各曝光、点击、加购和支付事件各自发生日，非曝光 cohort。",
        "answerability": "EVENT_DAY_RATIO_ONLY",
        "mustDisclose": ["事件日比率", "非 cohort", "期间", "dataAsOf"],
        "forbiddenClaims": ["正式转化率", "曝光 cohort 转化率", "推荐导致购买"],
    },
    "analytics_recommendation_quality_daily": {
        "grain": ["date", "product_id"],
        "dateOwnership": "各 VERIFIED 结果事件各自发生日。",
        "answerability": "EVENT_COUNTS_ONLY",
        "mustDisclose": ["事件日", "VERIFIED 归因", "期间", "dataAsOf"],
        "forbiddenClaims": ["长期用户收益", "因果效果", "推荐质量总分"],
    },
    "analytics_offer_quality_daily": {
        "grain": ["date", "product_id"],
        "dateOwnership": "用户绑定报价快照创建日。",
        "answerability": "SNAPSHOT_STATISTICS_ONLY",
        "mustDisclose": ["报价快照", "估算到手价", "期间", "dataAsOf"],
        "forbiddenClaims": ["最终成交价", "优惠承诺", "结算价格"],
    },
    "analytics_fulfillment_after_sales_daily": {
        "grain": ["date"],
        "dateOwnership": "订单状态计数归订单创建日；退款申请归创建日；退款完成归完成日。",
        "answerability": "MIXED_EVENT_DATE_WITH_STRONG_BOUNDARY",
        "mustDisclose": ["混合日期归属", "非履约 cohort", "期间", "dataAsOf"],
        "forbiddenClaims": ["当天实际发货量", "当天实际完成量", "订单 cohort 履约率"],
    },
    "analytics_inventory_forecast": {
        "grain": ["snapshot_date", "product_id", "sku_key"],
        "dateOwnership": "CURRENT_DATE 的预测输入快照，需求窗口为最近 28 个自然日。",
        "answerability": "PLANNING_INPUT_ONLY",
        "mustDisclose": ["人工补货建议", "数据覆盖度", "非采购指令", "dataAsOf"],
        "forbiddenClaims": ["统计置信概率", "自动采购指令", "确定会缺货", "正式需求预测"],
    },
}


def build_catalog() -> dict[str, Any]:
    if set(CATALOG) != set(COLUMN_SPECS) or set(CATALOG) != set(VIEW_POLICIES):
        raise ValueError("provisional catalog must cover exactly the ten runtime views")
    views: dict[str, Any] = {}
    for name, runtime in CATALOG.items():
        runtime_columns = set(runtime.get("columns") or {})
        governed_columns = set(COLUMN_SPECS[name])
        derived_columns = set(runtime.get("derived_metrics") or {})
        if runtime_columns | derived_columns != governed_columns:
            raise ValueError(
                f"catalog column drift for {name}: runtime={sorted(runtime_columns | derived_columns)} "
                f"provisional={sorted(governed_columns)}"
            )
        columns = {}
        for column_name, meaning in (runtime.get("columns") or {}).items():
            columns[column_name] = {
                "description": meaning,
                **deepcopy(COLUMN_SPECS[name][column_name]),
            }
        for column_name, spec in (runtime.get("derived_metrics") or {}).items():
            columns[column_name] = {
                "description": spec.get("definition"),
                "sqlExpression": spec.get("sql_expression"),
                **deepcopy(COLUMN_SPECS[name][column_name]),
            }
        views[name] = {
            "description": runtime["description"],
            "requiredPermission": "ANALYTICS_READ",
            "exportPermission": "ANALYTICS_EXPORT",
            "allowedRoles": ["ALL_AUTHENTICATED_ADMINS_WITH_PERMISSION"],
            "requiresDateFilter": bool(runtime.get("requires_date_filter")),
            "dateColumn": runtime.get("date_column"),
            "columns": columns,
            **deepcopy(VIEW_POLICIES[name]),
        }
    source_projection = {
        name: {
            "description": item["description"],
            "dateColumn": item.get("date_column"),
            "requiresDateFilter": item.get("requires_date_filter"),
            "columns": item.get("columns"),
            "derivedMetrics": item.get("derived_metrics"),
        }
        for name, item in CATALOG.items()
    }
    catalog: dict[str, Any] = {
        "schemaVersion": CATALOG_SCHEMA_VERSION,
        "catalogVersion": CATALOG_VERSION,
        "lifecycle": "PROVISIONAL",
        "effectiveAt": "2026-08-27T00:00:00+08:00",
        "development": True,
        "provisional": True,
        "unseen": False,
        "releaseGateEligible": False,
        "currency": DEFAULT_CURRENCY,
        "timezone": FIXED_TIMEZONE,
        "dataAsOfPolicy": "one read-only consistent snapshot per request",
        "decimalPolicy": {
            "jsonEncoding": "CANONICAL_STRING",
            "moneyDisplayScale": 2,
            "rounding": "ROUND_HALF_UP_FOR_PRESENTATION_ONLY",
            "tableAndExport": "PRESERVE_TYPED_STRING",
            "chart": "CREATE_NUMERIC_COPY_CLIENT_SIDE",
        },
        "answerPolicy": {
            "moneyDisclaimer": "暂定口径，仅供运营核对；不得作为结算或审计结论。",
            "requiredMetadata": ["catalogVersion", "dataAsOf", "period"],
            "outOfCatalog": "ABSTAIN",
            "permissionFailure": "DENY",
        },
        "source": {
            "runtimeCatalog": "app/services/analytics_catalog.py",
            "runtimeCatalogSha256": sha256_bytes(canonical_json_bytes(source_projection)),
            "viewMigration": "AI_Shop-admin/src/main/resources/db/migration/R__current_schema.sql",
            "viewMigrationSha256": sha256_file(ADMIN_DDL),
        },
        "views": views,
    }
    catalog["contentSha256"] = sha256_bytes(canonical_json_bytes(catalog))
    return catalog


def verify_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    content_hash = catalog.get("contentSha256")
    payload = deepcopy(catalog)
    payload.pop("contentSha256", None)
    checks = {
        "schemaVersion": catalog.get("schemaVersion") == CATALOG_SCHEMA_VERSION,
        "catalogVersion": catalog.get("catalogVersion") == CATALOG_VERSION,
        "lifecycle": catalog.get("lifecycle") == "PROVISIONAL",
        "tenViews": set(catalog.get("views") or {}) == set(CATALOG),
        "contentSha256": content_hash == sha256_bytes(canonical_json_bytes(payload)),
        "viewMigrationSha256": (
            (catalog.get("source") or {}).get("viewMigrationSha256") == sha256_file(ADMIN_DDL)
        ),
    }
    if not all(checks.values()):
        raise ValueError(f"provisional catalog verification failed: {checks}")
    return {"verified": True, "catalogVersion": CATALOG_VERSION, "checks": checks}


def write_catalog(path: Path, *, overwrite: bool = False) -> dict[str, Any]:
    catalog = build_catalog()
    write_json(path, catalog, overwrite=overwrite)
    return catalog
