from __future__ import annotations

import json
from datetime import datetime

import structlog

from app.constants import (
    ORDER_STATUS_NAMES,
    ORDER_STATUS_WAIT_PAYMENT,
)
from app.exceptions import PendingActionConflict
from app.services.evidence_refs import (
    action_capability_ref,
    after_sales_eligibility_ref,
    negative_lookup_ref,
    order_refs,
    pending_action_ref,
    product_no_result_ref,
    product_refs,
    product_search_constraint_ref,
)
from app.services.java_internal_client import java_internal_client
from app.services.order_service import order_service
from app.services.pending_action_service import pending_action_service
from app.services.product_search_pipeline import required_qualifier_evidence
from app.services.shopping_profile_service import (
    extract_profile,
    merge_profiles,
    shopping_profile_service,
)
from app.services.tool_invoke_result import ToolInvokeResult
from app.utils.biz_payload import build_action_confirm_payload

logger = structlog.get_logger()


def _structured_property_text(value: object, *, depth: int = 0) -> list[str]:
    """Collect only known structured property fields for exclusion auditing."""

    if depth > 3:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        result: list[str] = []
        for item in value[:80]:
            result.extend(_structured_property_text(item, depth=depth + 1))
        return result
    if not isinstance(value, dict):
        return []
    result = [
        str(value.get(key) or "")
        for key in (
            "property_name",
            "propertyName",
            "name",
            "property_value",
            "propertyValue",
            "value",
        )
    ]
    for key in ("property_values", "propertyValues", "properties", "values"):
        if key in value:
            result.extend(_structured_property_text(value[key], depth=depth + 1))
    if not any(
        key in value
        for key in (
            "property_name",
            "propertyName",
            "name",
            "property_value",
            "propertyValue",
            "value",
            "property_values",
            "propertyValues",
            "properties",
            "values",
        )
    ):
        for key, item in list(value.items())[:40]:
            result.append(str(key))
            result.extend(_structured_property_text(item, depth=depth + 1))
    return [item for item in result if item]


def _searchable_product_text(product: dict) -> str:
    fields = (
        "product_name",
        "productName",
        "product_desc",
        "productDesc",
        "description",
        "category",
        "categoryName",
        "brand",
    )
    direct = [str(product.get(key) or "") for key in fields]
    properties = [
        *_structured_property_text(product.get("property_values")),
        *_structured_property_text(product.get("propertyValues")),
        *_structured_property_text(product.get("properties")),
    ]
    return " ".join([*direct, *properties]).casefold()


def _search_constraint_evidence(
    products: list[dict],
    profile: dict | None,
    *,
    constraint_query: str | None = None,
) -> dict:
    """Expose a conservative audit of returned candidates and hard constraints.

    The catalog filter remains authoritative in the search pipeline.  This
    projection deliberately says what was checked on returned cards; it does
    not claim that an excluded item is absent from the full catalogue.
    """

    profile = merge_profiles(profile, extract_profile(constraint_query))
    excluded_brands = [
        str(value).strip()
        for value in profile.get("excludedBrands") or []
        if str(value).strip()
    ]
    excluded_terms = [
        str(value).strip()
        for value in profile.get("excludedTerms") or []
        if str(value).strip()
    ]
    exclusion_violating_ids: list[str] = []
    for product in products:
        product_id = str(
            product.get("product_id")
            or product.get("productId")
            or product.get("id")
            or ""
        )
        text = _searchable_product_text(product)
        brand = shopping_profile_service.resolve_known_brand(product, profile)
        brand_hit = any(
            str(value).casefold() in str(brand or "").casefold()
            or str(value).casefold() in text
            for value in excluded_brands
        )
        term_hit = any(str(value).casefold() in text for value in excluded_terms)
        if brand_hit or term_hit:
            exclusion_violating_ids.append(product_id)
    qualifier = required_qualifier_evidence(products, constraint_query)
    qualifier_violating_ids = list(
        qualifier["unverifiedRequiredQualifierProductIds"]
    )
    violating_ids = list(
        dict.fromkeys([*exclusion_violating_ids, *qualifier_violating_ids])
    )
    return {
        "type": "HARD_CONSTRAINT_AUDIT",
        "excludedBrands": excluded_brands,
        "excludedTerms": excluded_terms,
        "returnedCandidateCount": len(products),
        "exclusionViolatingReturnedProductIds": exclusion_violating_ids,
        "violatingReturnedProductIds": violating_ids,
        "returnedCandidatesSatisfyExclusions": not exclusion_violating_ids,
        **qualifier,
        "catalogAbsenceClaim": False,
    }

def _status_name(status: int | None) -> str:

    if status is None:
        return "未知"
    return ORDER_STATUS_NAMES.get(status, str(status))

def _propose_reply(
    label: str,
    pending: dict,
    *,
    source_refs: list[dict] | None = None,
) -> ToolInvokeResult:
    """Return a server-authored confirmation card and a small model observation.

    The action credential is intentionally present in the structured card and
    ``bizData``.  Keeping it in the tool observation is a compatibility bridge
    for older graph paths, but it is no longer the only transport contract.
    """
    token = str(pending.get("token") or "").strip()
    assistant, biz_data = build_action_confirm_payload(
        pending,
        intro=(
            f"已生成{label}确认卡片。请用一句话说明关键信息（勿重复工具原文、"
            "勿写【成功/失败】）。"
        ),
    )
    proposal_ref = pending_action_ref(pending)
    refs = [ref for ref in (source_refs or []) if isinstance(ref, dict)]
    if proposal_ref is not None:
        refs.append(proposal_ref)
    return ToolInvokeResult(
        content=(
            f"已生成{label}确认卡片。请确认后提交，"
            f"并在回复末尾附带【{token}】"
        ),
        biz_type="action_confirm",
        biz_data=biz_data,
        assistant_cards=assistant,
        source_refs=refs,
        contract_data={
            "type": "ACTION_CONFIRM",
            "actionType": pending.get("actionType"),
            "actionTokenPresent": bool(token),
        },
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

async def query_logistics(user_id: str, order_id: str) -> ToolInvokeResult:

    if not order_id:
        return ToolInvokeResult(
            content="【查询物流失败】请输入要查询物流的订单号",
            success=False,
            error_code="BAD_ARGS",
        )
    try:
        logistics = await java_internal_client.get_logistics(user_id, order_id)
        if not logistics:
            return ToolInvokeResult(
                content="【查询物流失败】订单物流信息不存在",
                biz_type="query_logistics",
                source_refs=[
                    negative_lookup_ref(
                        "logistics",
                        query={"orderId": order_id},
                        source="JAVA_LOGISTICS_SERVICE",
                    )
                ],
            )
        records = logistics.get("record_list") or logistics.get("recordList") or []
        latest_addr = ""
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
            content = header + chr(10) + "暂无物流轨迹明细。"
        else:
            table = "<table>" + chr(10)
            table += "<tr><th>时间</th><th>地点</th></tr>" + chr(10)
            table += chr(10).join(rows) + chr(10) + "</table>"
            latest = records[0] if records and isinstance(records[0], dict) else {}
            latest_addr = latest.get("record_address") or latest.get("recordAddress") or ""
            footer = (chr(10) + "当前包裹最新位置：" + str(latest_addr)) if latest_addr else ""
            content = header + chr(10) + table + footer

        return ToolInvokeResult(
            content=content,
            biz_type="query_logistics",
            source_refs=[
                {
                    "type": "logistics",
                    "id": str(order_id),
                    "orderId": str(order_id),
                    "matched": True,
                    "carrier": company,
                    "trackingNo": logistics_no,
                    "status": status_name,
                    "latestLocation": latest_addr or None,
                    "recordCount": len(records),
                    "source": "JAVA_LOGISTICS_SERVICE",
                }
            ],
        )
    except Exception:
        logger.exception("mcp_query_logistics_failed", user_id=user_id, order_id=order_id)
        return ToolInvokeResult(
            content="【查询物流失败】系统处理异常，请稍后重试或联系客服",
            success=False,
            error_code="TOOL_ERROR",
            biz_type="query_logistics",
        )



async def query_comment(user_id: str, order_id: str) -> ToolInvokeResult:

    if not order_id:
        return ToolInvokeResult(
            content="【查询评价失败】请输入要查询评价的订单号",
            success=False,
            error_code="BAD_ARGS",
        )
    try:
        row = await java_internal_client.get_comment(user_id, order_id)
        if not row:
            return ToolInvokeResult(
                content="【查询评价失败】订单评价不存在",
                biz_type="query_comment",
                source_refs=[
                    negative_lookup_ref(
                        "comment",
                        query={"orderId": order_id},
                        source="JAVA_COMMENT_SERVICE",
                    )
                ],
            )
        content = json.dumps(
            {k: (_fmt_dt(v) if isinstance(v, datetime) else v) for k, v in row.items()},
            ensure_ascii=False,
            default=str,
        )
        return ToolInvokeResult(
            content=content,
            biz_type="query_comment",
            source_refs=[
                {
                    "type": "comment",
                    "id": str(order_id),
                    "orderId": str(order_id),
                    "matched": True,
                    "commentStatus": row.get("status") or row.get("comment_status"),
                    "source": "JAVA_COMMENT_SERVICE",
                }
            ],
        )
    except Exception:
        logger.exception("mcp_query_comment_failed", user_id=user_id, order_id=order_id)
        return ToolInvokeResult(
            content="【查询评价失败】系统处理异常，请稍后重试或联系客服",
            success=False,
            error_code="TOOL_ERROR",
            biz_type="query_comment",
        )


async def query_refund_status(
    user_id: str,
    order_id: str | None = None,
    order_item_id: str | None = None,
) -> ToolInvokeResult:
    from app.services.tool_invoke_result import ToolInvokeResult

    if not order_id and not order_item_id:
        return ToolInvokeResult(
            content="【查询退款进度失败】请先选择退款订单或订单项",
            success=False,
            error_code="BAD_ARGS",
        )
    try:
        rows = await java_internal_client.get_refund_status(
            user_id,
            order_id=order_id,
            order_item_id=order_item_id,
        )
        if not rows:
            return ToolInvokeResult(
                content="该订单暂未查到退款申请记录。",
                biz_type="query_refund_status",
                source_refs=[
                    negative_lookup_ref(
                        "refund",
                        query={
                            "orderId": order_id,
                            "orderItemId": order_item_id,
                        },
                        source="JAVA_REFUND_SERVICE",
                    )
                ],
            )
        return ToolInvokeResult(
            content=json.dumps(rows, ensure_ascii=False, default=str),
            biz_type="query_refund_status",
            order_ids=[
                str(row.get("order_id") or "")
                for row in rows
                if row.get("order_id")
            ],
            source_refs=[
                {
                    "type": "refund",
                    "id": str(row.get("refund_id") or row.get("refundId") or row.get("order_id") or order_id or order_item_id),
                    "orderId": row.get("order_id") or row.get("orderId") or order_id,
                    "orderItemId": row.get("order_item_id") or row.get("orderItemId") or order_item_id,
                    "refundStatus": row.get("refund_status") or row.get("refundStatus") or row.get("status"),
                    "refundAmount": row.get("refund_amount") or row.get("refundAmount"),
                    "matched": True,
                    "source": "JAVA_REFUND_SERVICE",
                }
                for row in rows
                if isinstance(row, dict)
            ],
        )
    except Exception:
        logger.exception(
            "mcp_query_refund_status_failed",
            user_id=user_id,
            order_id=order_id,
            order_item_id=order_item_id,
        )
        return ToolInvokeResult(
            content="【查询退款进度失败】系统处理异常，请稍后重试或联系客服",
            success=False,
            error_code="TOOL_ERROR",
        )

async def query_user_coupons(user_id: str, status: int | None = None) -> ToolInvokeResult:

    if not user_id:
        return ToolInvokeResult(
            content="【查询优惠券失败】用户ID不能为空",
            success=False,
            error_code="BAD_ARGS",
        )
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
            return ToolInvokeResult(
                content="【查询优惠券成功】当前没有符合条件的优惠券",
                biz_type="query_user_coupons",
                source_refs=[
                    negative_lookup_ref(
                        "coupon",
                        query={"status": query_status, "scope": "AUTHENTICATED_USER"},
                        source="JAVA_COUPON_SERVICE",
                    )
                ],
            )
        return ToolInvokeResult(
            content=f"【查询优惠券成功】共 {len(result)} 张：{json.dumps(result, ensure_ascii=False)}",
            biz_type="query_user_coupons",
            source_refs=[
                {
                    "type": "coupon",
                    "id": str(item.get("userCouponId") or item.get("couponId") or ""),
                    "couponId": item.get("couponId"),
                    "couponName": item.get("couponName"),
                    "status": item.get("status"),
                    "validEndTime": item.get("validEndTime"),
                    "matched": True,
                    "scope": "AUTHENTICATED_USER",
                    "source": "JAVA_COUPON_SERVICE",
                }
                for item in result
                if item.get("userCouponId") or item.get("couponId")
            ],
        )
    except Exception:
        logger.exception("mcp_query_user_coupons_failed", user_id=user_id)
        return ToolInvokeResult(
            content="【查询优惠券失败】系统处理异常，请稍后重试",
            success=False,
            error_code="TOOL_ERROR",
            biz_type="query_user_coupons",
        )

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


async def _require_order_action_capability(
    action: str,
    order_id: str,
) -> tuple[dict, ToolInvokeResult | None]:
    """Fail closed unless Java explicitly allows this exact action/order."""

    try:
        decision = await java_internal_client.get_order_action_capability(
            action, order_id
        )
    except Exception:
        decision = {
            "decision": "UNAVAILABLE",
            "action": action,
            "order_id": order_id,
            "reason_code": "CAPABILITY_SERVICE_UNAVAILABLE",
        }
    outcome = str(decision.get("decision") or "UNAVAILABLE").upper()
    if outcome == "ALLOWED":
        return decision, None
    ref = action_capability_ref(decision)
    if outcome == "DENIED":
        content = "【操作资格核验】业务系统判定当前不可执行该操作；如状态刚变化，请刷新后重试"
        error_code = "ACTION_NOT_ALLOWED"
    elif outcome == "MANUAL_REVIEW":
        content = "【操作资格核验】该操作需要人工复核，请转人工处理"
        error_code = "ACTION_MANUAL_REVIEW"
    else:
        content = "【操作资格核验失败】暂时无法取得可核验的资格结论，未生成确认卡"
        error_code = "ACTION_CAPABILITY_UNAVAILABLE"
    return decision, ToolInvokeResult(
        content=content,
        success=False,
        error_code=error_code,
        source_refs=[ref] if ref else [],
    )

async def propose_cancel_order(
    user_id: str, order_id: str, run_id: str | None = None
) -> str | ToolInvokeResult:
    if not order_id:
        return "【取消订单失败】请输入要取消的订单号"
    try:
        order = await order_service.get_order(order_id)
        if not order:
            return "【取消订单失败】订单不存在"
        if order["user_id"] != user_id:
            return "【取消订单失败】您没有权限操作此订单"
        capability, rejected = await _require_order_action_capability(
            "CANCEL_ORDER", order_id
        )
        if rejected is not None:
            return rejected
        status = order.get("order_status")
        params = {
            "orderId": order_id,
            "orderAmount": float(order["amount"]),
            "orderStatusBefore": status,
            "payScene": order.get("pay_scene"),
            "capabilityDecision": {
                "decision": capability.get("decision"),
                "reasonCode": capability.get("reason_code")
                or capability.get("reasonCode"),
                "capabilityVersion": capability.get("capability_version")
                or capability.get("capabilityVersion"),
                "evaluatedAt": capability.get("evaluated_at")
                or capability.get("evaluatedAt"),
            },
            "orderItems": await _order_items_params(order_id, order),
        }
        pending = await pending_action_service.create_pending(
            "CANCEL_ORDER",
            user_id,
            params,
            (
                f"取消订单：订单 {order_id}，订单金额 {order['amount']} 元"
                if status == ORDER_STATUS_WAIT_PAYMENT
                else f"取消订单：订单 {order_id}，实付金额 {order['amount']} 元"
            ),
            run_id=run_id,
        )
        capability_ref = action_capability_ref(capability)
        return _propose_reply(
            "取消订单",
            pending,
            source_refs=[*order_refs([order]), *([capability_ref] if capability_ref else [])],
        )
    except PendingActionConflict as exc:
        return f"【取消订单失败】{exc}"
    except Exception:
        logger.exception(
            "mcp_propose_cancel_order_failed", user_id=user_id, order_id=order_id
        )
        return "【取消订单失败】系统处理异常，请稍后重试或联系客服"


async def propose_confirm_receipt(
    user_id: str, order_id: str, run_id: str | None = None
) -> str | ToolInvokeResult:

    if not order_id:
        return "【确认收货失败】请输入要确认收货的订单号"
    try:
        order = await order_service.get_order(order_id)
        if not order:
            return "【确认收货失败】订单不存在"
        if order["user_id"] != user_id:
            return "【确认收货失败】您没有权限操作此订单"
        capability, rejected = await _require_order_action_capability(
            "CONFIRM_RECEIPT", order_id
        )
        if rejected is not None:
            return rejected
        params = {
            "orderId": order_id,
            "orderAmount": float(order["amount"]),
            "payScene": order.get("pay_scene"),
            "capabilityDecision": {
                "decision": capability.get("decision"),
                "reasonCode": capability.get("reason_code")
                or capability.get("reasonCode"),
                "capabilityVersion": capability.get("capability_version")
                or capability.get("capabilityVersion"),
                "evaluatedAt": capability.get("evaluated_at")
                or capability.get("evaluatedAt"),
            },
            "orderItems": await _order_items_params(order_id, order),
        }
        pending = await pending_action_service.create_pending(
            "CONFIRM_RECEIPT",
            user_id,
            params,
            f"确认收货：订单 {order_id}，实付金额 {order['amount']} 元",
            run_id=run_id,
        )
        capability_ref = action_capability_ref(capability)
        return _propose_reply(
            "确认收货",
            pending,
            source_refs=[*order_refs([order]), *([capability_ref] if capability_ref else [])],
        )
    except PendingActionConflict as exc:
        return f"【确认收货失败】{exc}"
    except Exception:
        logger.exception(
            "mcp_propose_confirm_receipt_failed",
            user_id=user_id,
            order_id=order_id,
        )
        return "【确认收货失败】系统处理异常，请稍后重试或联系客服"

async def propose_refund(
    user_id: str, order_item_id: str, run_id: str | None = None
) -> str | ToolInvokeResult:

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
            owned_items = await java_internal_client.list_order_items(order_id)
            if len(owned_items) == 1:
                item = owned_items[0]
                normalized = str(item.get("order_item_id") or "")
            elif len(owned_items) > 1:
                lines = []
                for row in owned_items[:8]:
                    oid = row.get("order_item_id")
                    name = row.get("product_name") or "商品"
                    lines.append(f"- {name}（订单项ID：{oid}）")
                return (
                    "【退款失败】该订单有多个商品，请指定其中一个订单项ID后再核验退款资格：\n"
                    + "\n".join(lines)
                )
            else:
                return (
                    "【退款失败】未定位到可核验的订单项，尚未作出退款资格结论；"
                    "请确认订单项ID后重试。"
                )
        order_item_id = normalized
        if not item or not order_item_id:
            return "【退款失败】订单项不存在，请确认订单项ID是否正确（格式一般为：订单号_1）。"
        order = await order_service.get_order(item["order_id"])
        if not order or order["user_id"] != user_id:
            return "【退款失败】您没有权限操作此订单项"
        from app.services.after_sales_policy_service import after_sales_policy_service

        eligibility = await after_sales_policy_service.evaluate(
            user_id=user_id,
            action="REFUND",
            order_id=item["order_id"],
            order_item_id=order_item_id,
            evidence=[],
        )
        eligibility_ref = after_sales_eligibility_ref(eligibility)
        if str(eligibility.get("decision") or "").upper() != "ELIGIBLE":
            return ToolInvokeResult(
                content=(
                    "【退款资格核验】当前不符合退款资格"
                    if eligibility.get("decision") == "INELIGIBLE"
                    else "【退款资格核验】暂时无法取得可执行的资格结论，未生成确认卡"
                ),
                success=False,
                error_code=str(eligibility.get("decision") or "POLICY_UNAVAILABLE"),
                source_refs=[eligibility_ref] if eligibility_ref else [],
            )
        params = {
            "orderItemId": order_item_id,
            "orderId": item["order_id"],
            "refundAmount": float(item["item_amount"]),
            "payScene": order.get("pay_scene"),
            "eligibilityDecision": {
                key: eligibility.get(key)
                for key in (
                    "decisionId",
                    "decision",
                    "policyId",
                    "policyVersion",
                    "evaluatedAt",
                )
                if eligibility.get(key) is not None
            },
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
            run_id=run_id,
        )
        order_with_item = {**order, "items": [item]}
        return _propose_reply(
            "退款",
            pending,
            source_refs=[
                *order_refs([order_with_item]),
                *([eligibility_ref] if eligibility_ref else []),
            ],
        )
    except PendingActionConflict as exc:
        return f"【退款失败】{exc}"
    except Exception:
        logger.exception(
            "mcp_propose_refund_failed",
            user_id=user_id,
            order_item_id=order_item_id,
        )
        return "【退款失败】系统处理异常，请稍后重试或联系客服"

async def propose_product_review(
    user_id: str,
    order_id: str,
    content: str,
    star: int,
    run_id: str | None = None,
) -> str | ToolInvokeResult:

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
        capability, rejected = await _require_order_action_capability(
            "PRODUCT_REVIEW", order_id
        )
        if rejected is not None:
            return rejected
        params = {
            "orderId": order_id,
            "commentContent": content,
            "star": star,
            "payScene": order.get("pay_scene"),
            "capabilityDecision": {
                "decision": capability.get("decision"),
                "capabilityVersion": capability.get("capability_version")
                or capability.get("capabilityVersion"),
                "evaluatedAt": capability.get("evaluated_at")
                or capability.get("evaluatedAt"),
            },
            "orderItems": await _order_items_params(order_id, order),
        }
        pending = await pending_action_service.create_pending(
            "PRODUCT_REVIEW",
            user_id,
            params,
            f"提交评价：订单 {order_id}，{star} 星，内容「{_truncate(content)}」",
            run_id=run_id,
        )
        capability_ref = action_capability_ref(capability)
        return _propose_reply(
            "评价",
            pending,
            source_refs=[*order_refs([order]), *([capability_ref] if capability_ref else [])],
        )
    except PendingActionConflict as exc:
        return f"【评价失败】{exc}"
    except Exception:
        logger.exception(
            "mcp_propose_product_review_failed",
            user_id=user_id,
            order_id=order_id,
        )
        return "【评价失败】系统处理异常，请稍后重试或联系客服"

async def propose_recomment(
    user_id: str, order_id: str, content: str, run_id: str | None = None
) -> str | ToolInvokeResult:

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
        capability, rejected = await _require_order_action_capability(
            "RECOMMENT", order_id
        )
        if rejected is not None:
            return rejected
        params = {
            "orderId": order_id,
            "reCommentContent": content,
            "payScene": order.get("pay_scene"),
            "capabilityDecision": {
                "decision": capability.get("decision"),
                "capabilityVersion": capability.get("capability_version")
                or capability.get("capabilityVersion"),
                "evaluatedAt": capability.get("evaluated_at")
                or capability.get("evaluatedAt"),
            },
            "orderItems": await _order_items_params(order_id, order),
        }
        pending = await pending_action_service.create_pending(
            "RECOMMENT",
            user_id,
            params,
            f"提交追评：订单 {order_id}，内容「{_truncate(content)}」",
            run_id=run_id,
        )
        capability_ref = action_capability_ref(capability)
        return _propose_reply(
            "追评",
            pending,
            source_refs=[*order_refs([order]), *([capability_ref] if capability_ref else [])],
        )
    except PendingActionConflict as exc:
        return f"【追评失败】{exc}"
    except Exception:
        logger.exception(
            "mcp_propose_recomment_failed",
            user_id=user_id,
            order_id=order_id,
        )
        return "【追评失败】系统处理异常，请稍后重试或联系客服"


async def propose_create_support_case(
    user_id: str,
    category: str,
    description: str,
    order_id: str | None = None,
    order_item_id: str | None = None,
    image_asset_id: str | None = None,
    image_understanding: str | None = None,
    image_understanding_status: str | None = None,
    run_id: str | None = None,
    source_message_id: int | None = None,
    forced_handoff: bool = False,
    priority: str = "NORMAL",
) -> str | ToolInvokeResult:
    from app.services.support_case_service import support_case_service

    try:
        return await support_case_service.propose(
            user_id,
            category,
            description,
            order_id=order_id,
            order_item_id=order_item_id,
            image_asset_id=image_asset_id,
            image_understanding=image_understanding,
            image_understanding_status=image_understanding_status,
            run_id=run_id,
            source_message_id=source_message_id,
            forced_handoff=forced_handoff,
            priority=priority,
        )
    except PendingActionConflict as exc:
        return f"【创建工单失败】{exc}"
    except ValueError as exc:
        return f"【创建工单失败】{exc}"
    except Exception:
        logger.exception(
            "mcp_propose_support_case_failed",
            user_id=user_id,
            category=category,
        )
        return "【创建工单失败】系统处理异常，请稍后重试或联系客服"


async def query_support_cases(
    user_id: str, case_id: str | None = None
) -> "ToolInvokeResult":
    from app.services.support_case_service import support_case_service
    from app.services.tool_invoke_result import ToolInvokeResult

    try:
        rows = await support_case_service.list_for_user(user_id, case_id)
        if case_id and not rows:
            return ToolInvokeResult(
                content="【工单查询失败】工单不存在或无权查看",
                success=False,
                error_code="NOT_FOUND",
                biz_type="support_case_detail",
                source_refs=[
                    negative_lookup_ref(
                        "support_case",
                        query={"caseId": case_id, "scope": "AUTHENTICATED_USER"},
                        source="JAVA_SUPPORT_CASE_SERVICE",
                    )
                ],
            )
        if case_id:
            card = {"type": "SUPPORT_CASE_DETAIL", "case": rows[0]}
            return ToolInvokeResult(
                content=f"【工单查询成功】工单 {rows[0]['caseNo']} 状态为 {rows[0]['status']}",
                biz_type="support_case_detail",
                assistant_cards=json.dumps(card, ensure_ascii=False),
                source_refs=[
                    {
                        "type": "support_case",
                        "id": str(rows[0].get("caseId") or rows[0].get("id") or rows[0].get("caseNo")),
                        "caseId": rows[0].get("caseId") or rows[0].get("id"),
                        "caseNo": rows[0].get("caseNo"),
                        "status": rows[0].get("status"),
                        "matched": True,
                        "source": "JAVA_SUPPORT_CASE_SERVICE",
                    }
                ],
            )
        card = {"type": "SUPPORT_CASE_LIST", "cases": rows}
        return ToolInvokeResult(
            content=(
                f"【工单查询成功】共找到 {len(rows)} 条近期工单"
                if rows
                else "【工单查询成功】暂无售后工单"
            ),
            biz_type="support_case_list",
            assistant_cards=json.dumps(card, ensure_ascii=False),
            source_refs=(
                [
                    {
                        "type": "support_case",
                        "id": str(row.get("caseId") or row.get("id") or row.get("caseNo")),
                        "caseId": row.get("caseId") or row.get("id"),
                        "caseNo": row.get("caseNo"),
                        "status": row.get("status"),
                        "matched": True,
                        "source": "JAVA_SUPPORT_CASE_SERVICE",
                    }
                    for row in rows
                    if isinstance(row, dict)
                ]
                or [
                    negative_lookup_ref(
                        "support_case",
                        query={"scope": "AUTHENTICATED_USER"},
                        source="JAVA_SUPPORT_CASE_SERVICE",
                    )
                ]
            ),
        )
    except Exception:
        logger.exception("mcp_query_support_cases_failed", user_id=user_id)
        return ToolInvokeResult(
            content="【工单查询失败】系统处理异常，请稍后重试或联系客服",
            success=False,
            error_code="TOOL_ERROR",
            biz_type="support_case_list",
        )

async def tool_search_products(
    user_id: str,
    keyword: str,
    exclude_product_id: str | None = None,
    request_id: str | None = None,
    run_id: str | None = None,
    trusted_user_text: str | None = None,
) -> "ToolInvokeResult":

    from app.domain.recommendation.contracts import RecommendationRequest
    from app.services.episode_service import current_episode
    from app.services.product_search_query import build_product_query_scope
    from app.services.product_service import product_service
    from app.services.recommendation_contract_service import build_response
    from app.services.redis_service import redis_service
    from app.services.tool_invoke_result import ToolInvokeResult

    consult = await redis_service.get_consult_product(user_id)

    if not await redis_service.is_consult_active(user_id):
        consult = None
    constraint_query = trusted_user_text or keyword or ""
    query_scope = build_product_query_scope(constraint_query)
    assistant, biz_data, biz_type, products, source = await product_service.search_products(
        user_id,
        keyword,
        user_text=constraint_query,
        consult_product=consult,
        exclude_product_id=exclude_product_id,
        request_id=request_id,
    )
    from app.services.product_service import format_search_tool_message
    from app.services.shopping_mission_service import shopping_mission_service
    from app.services.shopping_profile_service import shopping_profile_service

    mission = await shopping_mission_service.load(user_id)
    effective_profile = await shopping_profile_service.get_effective_profile(user_id)
    content = format_search_tool_message(
        keyword,
        consult,
        products,
        source,
        profile=effective_profile,
        mission=mission,
        constraint_query=constraint_query,
    )
    constraint_evidence = _search_constraint_evidence(
        products,
        effective_profile,
        constraint_query=constraint_query,
    )
    request_id = str(
        request_id
        or
        next(
            (
                product.get("request_id") or product.get("requestId")
                for product in products
                if product.get("request_id") or product.get("requestId")
            ),
            "",
        )
        or f"req_{(current_episode().run_id if current_episode() else 'unbound')}"
    )
    run_id = str(
        run_id
        or (current_episode().run_id if current_episode() else request_id.removeprefix("req_"))
    )
    source_refs = product_refs(
        products,
        request_id=request_id,
        source="JAVA_GATEWAY",
    )
    if not products:
        source_refs = [
            product_no_result_ref(
                keyword or "",
                result_source=source or "constraint_miss",
                request_id=request_id,
                query_scope=query_scope,
                # ``none`` can also mean a provider returned no usable data;
                # keep that case visible but do not call it authoritative.
                authoritative=source not in {"", "none"},
            )
        ]
    constraint_ref = product_search_constraint_ref(
        constraint_evidence,
        request_id=request_id,
        source="JAVA_GATEWAY",
    )
    if constraint_ref is not None:
        # Source refs are bounded for persistence. Preserve the newest audit
        # alongside at most 29 product/no-result refs rather than leaving the
        # constraint only in the opaque recommendation trace.
        source_refs = [*source_refs[:29], constraint_ref]
    contract = build_response(
        RecommendationRequest(
            requestId=request_id,
            runId=run_id,
            mode="TEXT",
            query=keyword or "商品推荐",
        ),
        run_id=run_id,
        products=products,
        status=(
            "CLARIFICATION_REQUIRED"
            if source == "clarify"
            else "COMPLETED"
            if products
            else "NO_RESULT"
        ),
        fallback_used=source in {"rrf_fallback", "category", "browse", "hot_sale_explicit"},
        trace={
            "source": source,
            "constraintEvidence": constraint_evidence,
            "queryScope": query_scope,
        },
        message=content if not products else None,
    ).model_dump(mode="json", by_alias=True)
    if not products:
        return ToolInvokeResult(
            content=content,
            biz_type=biz_type,
            assistant_cards=assistant if assistant and assistant != "[]" else None,
            contract_data=contract,
            source_refs=source_refs,
        )

    names = [str(p.get("product_name") or p.get("productName") or "") for p in products]
    ids = [str(p.get("product_id") or p.get("productId") or "") for p in products if p.get("product_id") or p.get("productId")]
    return ToolInvokeResult(
        content=content,
        biz_type=biz_type,
        biz_data=biz_data,
        assistant_cards=assistant,
        product_ids=ids,
        product_names=[n for n in names if n],
        contract_data=contract,
        source_refs=source_refs,
    )

async def tool_query_orders(user_id: str, order_id: str | None = None) -> "ToolInvokeResult":

    import json

    from app.services.tool_invoke_result import ToolInvokeResult

    assistant, biz_data, biz_type = await order_service.query_orders(user_id, order_id or None)
    if assistant == "[]":
        return ToolInvokeResult(
            content=(
                "【订单查询】本次按当前登录账户查询未返回订单，但这不能证明订单不存在。"
                "请提供订单号、商品名称或大致下单时间后重试；本次不会取消或修改任何订单。"
            ),
            biz_type=biz_type,
            source_refs=[
                negative_lookup_ref(
                    "order",
                    query={"orderId": order_id, "scope": "AUTHENTICATED_USER"},
                    source="JAVA_ORDER_SERVICE",
                )
            ],
        )
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
            source_refs=order_refs(cards, source="JAVA_ORDER_SERVICE"),
        )
    except json.JSONDecodeError:
        return ToolInvokeResult(
            content=f"【订单查询】{assistant[:300]}",
            biz_type=biz_type,
            biz_data=biz_data,
        )

async def tool_get_product_detail(user_id: str, product_id: str) -> ToolInvokeResult:

    from app.constants import PRODUCT_STATUS_ON_SALE
    from app.services.java_internal_client import java_internal_client

    _ = user_id
    row = await java_internal_client.get_product_detail(product_id)
    if not row:
        return ToolInvokeResult(
            content=f"【商品不存在】productId={product_id}",
            success=False,
            error_code="NOT_FOUND",
            biz_type="product_detail",
            source_refs=[
                negative_lookup_ref(
                    "product_detail",
                    query={"productId": product_id},
                    source="JAVA_PRODUCT_SERVICE",
                )
            ],
        )
    if row.get("status") != PRODUCT_STATUS_ON_SALE:
        content = f"【商品已下架】{row.get('product_name') or product_id}"
    else:
        desc = (row.get("product_desc") or "")[:200]
        content = (
            f"商品：{row.get('product_name')} | ID：{row.get('product_id')} | "
            f"价格：{row.get('min_price')}~{row.get('max_price')}元 | "
            f"销量：{row.get('total_sale') or 0} | 简介：{desc}"
        )
    return ToolInvokeResult(
        content=content,
        biz_type="product_detail",
        source_refs=product_refs([row], source="JAVA_PRODUCT_SERVICE"),
    )


async def tool_compare_products(
    user_id: str, product_ids: list[str]
) -> "ToolInvokeResult":
    from app.services.product_comparison_service import (
        ProductComparisonError,
        product_comparison_service,
    )
    from app.services.tool_invoke_result import ToolInvokeResult

    try:
        return await product_comparison_service.compare(user_id, product_ids)
    except ProductComparisonError as exc:
        return ToolInvokeResult(
            content=f"【商品比较失败】{exc}",
            success=False,
            error_code=exc.code,
            biz_type="product_comparison",
        )
