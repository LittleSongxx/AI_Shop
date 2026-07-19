import json

from datetime import datetime

from app.constants import (
    CONFIRM_RECEIPT_ORDER_STATUSES,
    ORDER_ITEM_STATUS_NORMAL,
    ORDER_STATUS_NAMES,
    REFUNDABLE_ORDER_STATUSES,
    REVIEWABLE_ORDER_STATUSES,
)

from app.services.java_internal_client import java_internal_client
from app.services.order_service import order_service
from app.services.pending_action_service import pending_action_service

def _status_name(status: int | None) -> str:

    if status is None:
        return "未知"
    return ORDER_STATUS_NAMES.get(status, str(status))

def _propose_reply(label: str, pending: dict) -> str:

    return (
        f"已生成{label}确认卡片。请用一句话说明关键信息（勿重复工具原文、勿写【成功/失败】），"
        f"并在回复末尾附带【{pending['token']}】"
    )

def _truncate(text: str, max_len: int = 40) -> str:

    if not text or len(text) <= max_len:
        return text
    return text[:max_len] + "…"

def _fmt_dt(value) -> str | None:

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)

def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None

async def query_logistics(user_id: str, order_id: str) -> str:

    if not order_id:
        return "【查询物流失败】请输入要查询物流的订单号"
    try:
        logistics = await java_internal_client.get_logistics(user_id, order_id)
        if not logistics:
            return "【查询物流失败】订单物流信息不存在"
        records = logistics.get("record_list") or logistics.get("recordList") or []
        company = logistics.get("logistics_company") or logistics.get("logisticsCompany") or "快递"
        logistics_no = logistics.get("logistics_no") or logistics.get("logisticsNo") or "-"
        status = logistics.get("logistics_status")
        if status is None:
            status = logistics.get("logisticsStatus")
        status_name = {
            0: "待揽收",
            1: "已揽收",
            2: "运输中",
            3: "派送中",
            4: "已签收",
            5: "异常",
        }.get(int(status) if status is not None else -1, "运输中")

        rows: list[str] = []
        for r in records:
            if not isinstance(r, dict):
                continue
            t = r.get("record_time") or r.get("recordTime") or ""
            if hasattr(t, "strftime"):
                t = t.strftime("%Y-%m-%d %H:%M:%S")
            addr = r.get("record_address") or r.get("recordAddress") or ""
            rows.append("<tr><td>%s</td><td>%s</td></tr>" % (t, addr))

        header = "【订单%s查询物流成功】" % order_id
        header += chr(10) + "承运商：%s，运单号：%s，状态：%s" % (company, logistics_no, status_name)
        if not rows:
            return header + chr(10) + "暂无物流轨迹明细。"

        table = "<table>" + chr(10)
        table += "<tr><th>时间</th><th>地点</th></tr>" + chr(10)
        table += chr(10).join(rows) + chr(10) + "</table>"
        latest = records[0] if records and isinstance(records[0], dict) else {}
        latest_addr = latest.get("record_address") or latest.get("recordAddress") or ""
        footer = (chr(10) + "当前包裹最新位置：" + str(latest_addr)) if latest_addr else ""
        return header + chr(10) + table + footer
    except Exception as e:
        return "【查询物流失败】系统处理异常，请稍后重试或联系客服。错误信息：%s" % e



async def query_comment(user_id: str, order_id: str) -> str:

    if not order_id:
        return "【查询评价失败】请输入要查询评价的订单号"
    try:
        row = await java_internal_client.get_comment(user_id, order_id)
        if not row:
            return "【查询评价失败】订单评价不存在"
        return json.dumps(
            {k: (_fmt_dt(v) if isinstance(v, datetime) else v) for k, v in row.items()},
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:
        return f"【查询评价失败】系统处理异常，请稍后重试或联系客服。错误信息：{e}"

async def query_user_coupons(user_id: str, status: int | None = None) -> str:

    if not user_id:
        return "【查询优惠券失败】用户ID不能为空"
    try:
        query_status = 0 if status is None else status
        rows = await java_internal_client.list_user_coupons(user_id)

        result = []
        now = datetime.now()
        for r in rows:
            dynamic = r.get("uc_status")
            if dynamic is None:
                dynamic = r.get("status")
            end = _parse_dt(r.get("valid_end_time"))
            if dynamic == 0 and end and end < now:
                dynamic = 2
            if query_status == 0 and dynamic != 0:
                continue
            if query_status == 2 and dynamic != 2:
                continue
            if query_status not in (0, 2) and dynamic != query_status:
                continue
            result.append({
                "userCouponId": r.get("user_coupon_id"),
                "couponId": r.get("coupon_id"),
                "couponName": r.get("coupon_name"),
                "couponType": r.get("coupon_type"),
                "thresholdAmount": float(r["threshold_amount"]) if r.get("threshold_amount") is not None else None,
                "discountAmount": float(r["discount_amount"]) if r.get("discount_amount") is not None else None,
                "discountRate": float(r["discount_rate"]) if r.get("discount_rate") is not None else None,
                "validStartTime": _fmt_dt(r.get("valid_start_time")),
                "validEndTime": _fmt_dt(r.get("valid_end_time")),
                "status": dynamic,
            })
            if len(result) >= 20:
                break

        if not result:
            return "【查询优惠券成功】当前没有符合条件的优惠券"
        return f"【查询优惠券成功】共 {len(result)} 张：{json.dumps(result, ensure_ascii=False)}"
    except Exception:
        return "【查询优惠券失败】系统处理异常，请稍后重试"

async def _order_items_params(order_id: str, order: dict | None = None) -> list[dict]:

    items_map = await order_service._fetch_order_items([order_id])
    items = items_map.get(order_id, [])
    return [
        {
            "orderItemId": i.get("order_item_id"),
            "productName": i.get("product_name"),
            "cover": i.get("cover", "").split(",")[0] if i.get("cover") else None,
            "propertyInfo": i.get("property_info"),
        }
        for i in items[:5]
    ]

async def propose_confirm_receipt(user_id: str, order_id: str) -> str:

    if not order_id:
        return "【确认收货失败】请输入要确认收货的订单号"
    try:
        order = await order_service.get_order(order_id)
        if not order:
            return "【确认收货失败】订单不存在"
        if order["user_id"] != user_id:
            return "【确认收货失败】您没有权限操作此订单"
        st = order["order_status"]
        if st not in CONFIRM_RECEIPT_ORDER_STATUSES:
            return (
                f"【确认收货失败】当前订单状态无法确认收货，当前状态：{_status_name(st)}"
            )
        params = {
            "orderId": order_id,
            "orderAmount": float(order["amount"]),
            "payScene": order.get("pay_scene"),
            "orderItems": await _order_items_params(order_id, order),
        }
        pending = await pending_action_service.create_pending(
            "CONFIRM_RECEIPT",
            user_id,
            params,
            f"确认收货：订单 {order_id}，实付金额 {order['amount']} 元",
        )
        return _propose_reply("确认收货", pending)
    except Exception as e:
        return f"【确认收货失败】系统处理异常，请稍后重试或联系客服。错误信息：{e}"

async def propose_refund(user_id: str, order_item_id: str) -> str:

    if not order_item_id:
        return "【退款失败】请输入要退款的订单项ID"
    try:
        from app.utils.order_ids import extract_order_id, extract_order_item_id

        # Normalize: keep xxx_1 as item id; bare order id falls back below.
        normalized = extract_order_item_id(order_item_id) or (order_item_id or "").strip()
        item = await order_service.get_order_item(normalized)
        if not item:
            # LLM / force path often passes orderId instead of orderItemId.
            order_id = extract_order_id(normalized) or normalized
            refundable = await order_service.list_refundable_items(user_id, order_id)
            if len(refundable) == 1:
                item = refundable[0]
                normalized = str(item.get("order_item_id") or "")
            elif len(refundable) > 1:
                lines = []
                for row in refundable[:8]:
                    oid = row.get("order_item_id")
                    name = row.get("product_name") or "商品"
                    lines.append(f"- {name}（订单项ID：{oid}）")
                return (
                    "【退款失败】该订单有多个可退款商品，请指定其中一个订单项ID后再试：\n"
                    + "\n".join(lines)
                )
            else:
                order = await order_service.get_order(order_id)
                if order and order.get("user_id") == user_id:
                    st = order.get("order_status")
                    return (
                        f"【退款失败】订单存在，但当前状态为{_status_name(st)}，"
                        "仅待发货/已发货/部分退款订单可申请退款。"
                        "若订单项ID形如「订单号_1」，请使用完整订单项ID重试。"
                    )
                return (
                    "【退款失败】订单项不存在，请确认订单项ID是否正确"
                    "（格式一般为：订单号_1）。"
                )
        order_item_id = normalized
        if not item or not order_item_id:
            return "【退款失败】订单项不存在，请确认订单项ID是否正确（格式一般为：订单号_1）。"
        order = await order_service.get_order(item["order_id"])
        if not order or order["user_id"] != user_id:
            return "【退款失败】您没有权限操作此订单项"
        st = order.get("order_status")
        if st not in REFUNDABLE_ORDER_STATUSES:
            return (
                f"【退款失败】当前订单状态为{_status_name(st)}，"
                "仅待发货/已发货/部分退款订单可申请退款"
            )
        if item.get("order_item_status") != ORDER_ITEM_STATUS_NORMAL:
            return "【退款失败】当前订单项已退款，无法重复申请"
        params = {
            "orderItemId": order_item_id,
            "orderId": item["order_id"],
            "refundAmount": float(item["item_amount"]),
            "payScene": order.get("pay_scene"),
            "orderItems": [{
                "orderItemId": order_item_id,
                "productName": item.get("product_name"),
                "cover": (item.get("cover") or "").split(",")[0] or None,
                "propertyInfo": item.get("property_info"),
                "itemAmount": float(item["item_amount"]),
                "buyCount": item.get("buy_count"),
            }],
        }
        name = item.get("product_name") or "商品"
        pending = await pending_action_service.create_pending(
            "REFUND",
            user_id,
            params,
            f"退款：订单项 {order_item_id}（{name}），金额 {item['item_amount']} 元",
        )
        return _propose_reply("退款", pending)
    except Exception as e:
        return f"【退款失败】系统处理异常，请稍后重试或联系客服。错误信息：{e}"

async def propose_product_review(user_id: str, order_id: str, content: str, star: int) -> str:

    if not order_id:
        return "【评价失败】请输入要评价的订单号"
    if not content:
        return "【评价失败】请输入评价内容"
    if star is None or star < 1 or star > 5:
        return "【评价失败】评价星级必须是1-5的整数"
    try:
        order = await order_service.get_order(order_id)
        if not order:
            return "【评价失败】订单不存在"
        if order["user_id"] != user_id:
            return "【评价失败】您没有权限评价此订单"
        st = order["order_status"]
        if st not in REVIEWABLE_ORDER_STATUSES:
            return f"【评价失败】当前订单状态为{_status_name(st)}，订单完成后才能评价"
        params = {
            "orderId": order_id,
            "commentContent": content,
            "star": star,
            "payScene": order.get("pay_scene"),
            "orderItems": await _order_items_params(order_id, order),
        }
        pending = await pending_action_service.create_pending(
            "PRODUCT_REVIEW",
            user_id,
            params,
            f"提交评价：订单 {order_id}，{star} 星，内容「{_truncate(content)}」",
        )
        return _propose_reply("评价", pending)
    except Exception as e:
        return f"【评价失败】系统处理异常，请稍后重试或联系客服。错误信息：{e}"

async def propose_recomment(user_id: str, order_id: str, content: str) -> str:

    if not order_id:
        return "【追评失败】请输入要追评的订单号"
    if not content:
        return "【追评失败】请输入追评内容"
    try:
        order = await order_service.get_order(order_id)
        if not order:
            return "【追评失败】订单不存在"
        if order["user_id"] != user_id:
            return "【追评失败】您没有权限评价此订单"
        st = order["order_status"]
        if st not in REVIEWABLE_ORDER_STATUSES:
            return f"【追评失败】当前订单状态为{_status_name(st)}，订单完成后才能追评"
        params = {
            "orderId": order_id,
            "reCommentContent": content,
            "payScene": order.get("pay_scene"),
            "orderItems": await _order_items_params(order_id, order),
        }
        pending = await pending_action_service.create_pending(
            "RECOMMENT",
            user_id,
            params,
            f"提交追评：订单 {order_id}，内容「{_truncate(content)}」",
        )
        return _propose_reply("追评", pending)
    except Exception as e:
        return f"【追评失败】系统处理异常，请稍后重试或联系客服。错误信息：{e}"

async def tool_search_products(
    user_id: str,
    keyword: str,
    exclude_product_id: str | None = None,
) -> "ToolInvokeResult":

    from app.services.product_service import product_service
    from app.services.redis_service import redis_service
    from app.services.tool_invoke_result import ToolInvokeResult

    consult = await redis_service.get_consult_product(user_id)

    if not await redis_service.is_consult_active(user_id):
        consult = None
    assistant, biz_data, biz_type, products, source = await product_service.search_products(
        user_id,
        keyword,
        user_text=keyword or "",
        consult_product=consult,
        exclude_product_id=exclude_product_id,
    )
    if not products:
        return ToolInvokeResult(content="【搜索结果】未找到相关商品。")
    from app.services.product_service import format_search_tool_message

    names = [str(p.get("product_name") or p.get("productName") or "") for p in products]
    ids = [str(p.get("product_id") or p.get("productId") or "") for p in products if p.get("product_id") or p.get("productId")]
    content = format_search_tool_message(keyword, consult, products, source)
    return ToolInvokeResult(
        content=content,
        biz_type=biz_type,
        biz_data=biz_data,
        assistant_cards=assistant,
        product_ids=ids,
        product_names=[n for n in names if n],
    )

async def tool_query_orders(user_id: str, order_id: str | None = None) -> "ToolInvokeResult":

    from app.services.tool_invoke_result import ToolInvokeResult
    import json

    assistant, biz_data, biz_type = await order_service.query_orders(user_id, order_id or None)
    if assistant == "[]":
        return ToolInvokeResult(content="【订单查询】未找到相关订单。")
    try:
        cards = json.loads(assistant)
        order_ids = [str(c.get("orderId") or c.get("order_id") or "") for c in cards if isinstance(c, dict)]
        order_ids = [oid for oid in order_ids if oid]
        summary = "、".join(order_ids[:5])
        return ToolInvokeResult(
            content=f"【订单查询】找到 {len(cards)} 笔订单：{summary}",
            biz_type=biz_type,
            biz_data=biz_data,
            assistant_cards=assistant,
            order_ids=order_ids,
        )
    except json.JSONDecodeError:
        return ToolInvokeResult(content=f"【订单查询】{assistant[:300]}", biz_type=biz_type, biz_data=biz_data)

async def tool_get_product_detail(user_id: str, product_id: str) -> str:

    from app.services.product_service import product_service

    _ = user_id
    return await product_service.get_product_detail_text(product_id)
