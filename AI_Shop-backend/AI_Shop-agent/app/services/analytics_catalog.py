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
}


def catalog_prompt() -> str:
    lines: list[str] = []
    for name, item in CATALOG.items():
        columns = item["columns"]
        definitions = ", ".join(f"{column}={meaning}" for column, meaning in columns.items())
        lines.append(f"{name}: {item['description']} 字段: {definitions}")
    return "\n".join(lines)


def allowed_columns(view: str) -> frozenset[str]:
    item = CATALOG.get(view) or {}
    columns = item.get("columns") or {}
    return frozenset(str(name).lower() for name in columns)
