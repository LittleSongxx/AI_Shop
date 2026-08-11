from __future__ import annotations

CATALOG: dict[str, dict[str, object]] = {
    "analytics_sales_daily": {
        "description": "每日已支付订单和已完成退款。净支付额=支付总额-已完成退款额。",
        "date_column": "date",
        "requires_date_filter": True,
        "columns": {
            "date": "业务日期",
            "paid_order_count": "状态为已付款、已发货、已完成、已退款、部分退款或待评价的订单数",
            "gross_paid_amount": "上述订单的订单实付金额合计",
            "completed_refund_count": "退款 Saga 状态为 COMPLETED 的退款单数",
            "completed_refund_amount": "已完成退款金额合计",
            "net_paid_amount": "支付总额减已完成退款额",
        },
    },
    "analytics_product_sales_daily": {
        "description": "每日商品销售表现，商品名称使用下单时快照，不包含用户信息。",
        "date_column": "date",
        "requires_date_filter": True,
        "columns": {
            "date": "订单日期",
            "product_id": "商品 ID",
            "product_name": "下单时商品名称快照",
            "paid_units": "有效支付订单中的购买件数",
            "gross_item_amount": "订单项行金额合计；行金额在下单时已包含购买数量",
            "refunded_units": "退款 Saga 在当天完成的商品件数",
        },
    },
    "analytics_inventory_risk": {
        "description": "当前 SKU 库存风险快照；stock<=0 为缺货，1..10 为低库存，其余正常。",
        "date_column": "snapshot_date",
        "requires_date_filter": False,
        "columns": {
            "snapshot_date": "库存快照日期",
            "product_id": "商品 ID",
            "product_name": "商品名称",
            "property_value_id_hash": "SKU 属性组合哈希",
            "stock": "当前可用库存",
            "risk_level": "OUT_OF_STOCK、LOW_STOCK 或 NORMAL",
        },
        "derived_metrics": {
            "stockout_sku_count": {
                "definition": (
                    "缺货 SKU 数量，固定口径为 stock<=0；当前视图只保留当前快照，"
                    "历史日期未返回时必须披露为数据缺口"
                ),
                "sql_expression": "SUM(CASE WHEN stock <= 0 THEN 1 ELSE 0 END)",
            }
        },
    },
    "analytics_agent_quality_daily": {
        "description": "按日、Agent 和意图聚合的运行质量、延迟、token 与成本。",
        "date_column": "date",
        "requires_date_filter": True,
        "columns": {
            "date": "运行日期",
            "agent_id": "Agent 标识",
            "intent": "业务意图",
            "run_count": "运行总数",
            "success_count": "成功运行数",
            "failure_count": "失败或取消运行数",
            "human_handoff_count": "进入人工处理的运行数",
            "avg_latency_ms": "平均端到端延迟毫秒",
            "input_tokens": "输入 token 合计",
            "output_tokens": "输出 token 合计",
            "cost_cny": "按配置价格计算的人民币成本",
        },
    },
    "analytics_tool_quality_daily": {
        "description": "按日、Agent 和工具聚合的调用成功率与延迟。",
        "date_column": "date",
        "requires_date_filter": True,
        "columns": {
            "date": "调用日期",
            "agent_id": "执行工具的 Agent 标识",
            "tool_name": "工具名称",
            "call_count": "调用总数",
            "success_count": "成功调用数",
            "failure_count": "失败调用数",
            "avg_latency_ms": "平均调用延迟毫秒",
        },
    },
    "analytics_recommendation_funnel_daily": {
        "description": "每日推荐曝光、点击、加购和支付漏斗；只接受服务端验证过的归因链。",
        "date_column": "date",
        "requires_date_filter": True,
        "columns": {
            "date": "结果事件发生日期",
            "retrieval_mode": "text、visual 等推荐检索模式",
            "impression_count": "已持久化推荐曝光数",
            "click_count": "已持久化推荐点击数",
            "add_to_cart_count": "归因到有效曝光的加购事件数",
            "payment_count": "归因到有效曝光的支付事件数",
            "click_through_rate": "点击数除以曝光数；曝光为零时为 0",
            "cart_rate": "归因加购数除以曝光数；曝光为零时为 0",
            "payment_rate": "归因支付数除以曝光数；曝光为零时为 0",
        },
    },
    "analytics_recommendation_quality_daily": {
        "description": "每日推荐长期质量，联合退款、退货、低分评价和售后联系，不把短期转化等同于用户收益。",
        "date_column": "date",
        "requires_date_filter": True,
        "columns": {
            "date": "结果事件发生日期",
            "product_id": "商品 ID；不包含用户标识",
            "payment_count": "归因支付事件数",
            "refund_count": "归因退款完成事件数",
            "return_count": "归因退货事件数",
            "negative_review_count": "评分不高于 2 的评价事件数",
            "support_contact_count": "推荐后售后联系事件数",
            "repeat_purchase_count": "归因复购事件数",
        },
    },
    "analytics_offer_quality_daily": {
        "description": "每日 Agent 单 SKU 报价快照质量，包含报价量、优惠可验证率和可购买报价覆盖。",
        "date_column": "date",
        "requires_date_filter": True,
        "columns": {
            "date": "报价快照创建日期",
            "product_id": "商品 ID",
            "quote_count": "用户绑定报价快照数量",
            "coupon_available_count": "核验到单 SKU 可用优惠的报价数量",
            "avg_base_price": "报价快照中的平均 SKU 基础价",
            "avg_estimated_payable": "应用单 SKU 最优可验证优惠后的平均估算到手价",
            "in_stock_quote_count": "快照时库存可购买的报价数量",
        },
    },
    "analytics_fulfillment_after_sales_daily": {
        "description": "每日履约与售后结果，包含发货、完成、取消、退款申请及退款完成。",
        "date_column": "date",
        "requires_date_filter": True,
        "columns": {
            "date": "订单或退款业务日期",
            "paid_order_count": "当天创建且进入已支付及后续状态的订单数",
            "shipped_order_count": "当前状态为已发货的订单数",
            "completed_order_count": "当前状态为已完成或待评价的订单数",
            "cancelled_order_count": "当前状态为交易取消或关闭的订单数",
            "refund_request_count": "当天创建的退款请求数",
            "refund_completed_count": "当天完成的退款请求数",
            "refund_completed_amount": "当天完成退款金额",
        },
    },
    "analytics_inventory_forecast": {
        "description": "SKU 级库存预测输入和确定性补货量；28 天 EWMA 净需求，ROP=日需求×交期+安全库存，按 MOQ 向上取整。",
        "date_column": "snapshot_date",
        "requires_date_filter": False,
        "columns": {
            "snapshot_date": "预测快照日期",
            "product_id": "商品 ID",
            "product_name": "商品名称",
            "sku_key": "SKU 属性组合哈希",
            "risk_level": "当前库存风险：OUT_OF_STOCK、LOW_STOCK 或 NORMAL",
            "current_stock": "当前 SKU 可用库存",
            "inbound_quantity": "计划中且尚未到货的在途数量",
            "ewma_daily_demand": "最近 28 天按时间衰减计算的日均净支付需求",
            "lead_time_days": "人工维护的供应交期天数",
            "safety_stock": "人工维护的安全库存",
            "review_period_days": "补货复核周期，默认 14 天",
            "min_order_quantity": "最小起订量 MOQ",
            "reorder_point": "日需求乘交期加安全库存",
            "suggested_replenish_quantity": "覆盖交期和复核周期后按 MOQ 向上取整的人工建议量",
            "coverage_days": "当前库存加在途量可覆盖的预测天数",
            "confidence": "由有效销售天数形成的预测置信度 0 到 1",
        },
    },
}


def catalog_prompt() -> str:
    lines: list[str] = []
    for name, item in CATALOG.items():
        columns = item["columns"]
        definitions = ", ".join(f"{column}={meaning}" for column, meaning in columns.items())
        derived = item.get("derived_metrics") or {}
        derived_definitions = ", ".join(
            f"{metric}={spec.get('definition')}；固定表达式={spec.get('sql_expression')}"
            for metric, spec in derived.items()
        )
        derived_text = f" 受治理派生指标: {derived_definitions}" if derived_definitions else ""
        lines.append(f"{name}: {item['description']} 字段: {definitions}{derived_text}")
    return "\n".join(lines)


def allowed_columns(view: str) -> frozenset[str]:
    item = CATALOG.get(view) or {}
    columns = item.get("columns") or {}
    return frozenset(str(name).lower() for name in columns)


def allowed_plan_fields(view: str) -> frozenset[str]:
    """Fields a semantic plan may select, including governed derived metrics."""

    item = CATALOG.get(view) or {}
    derived = item.get("derived_metrics") or {}
    return allowed_columns(view) | frozenset(str(name).lower() for name in derived)
