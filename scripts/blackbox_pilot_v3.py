#!/usr/bin/env python3
"""Run the timing-safe 1x2 AI + Java storefront black-box protocol."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any

import blackbox_pilot_v2 as pilot

PROTOCOL_VERSION = "v3"
RUN_ROOT = pilot.ROOT / "run" / "blackbox-pilot-v3"
COHORT_DESCRIPTION = "一个模型系列、两个隔离会话；AI 与 Java 电商底座"

_AI_ORDER = (
    "RAG-COUPON-01",
    "RAG-PAYMENT-01",
    "RAG-AFTERSALES-01",
    "SHOP-DETAIL-01",
    "SHOP-GUITAR-01",
    "SHOP-NORESULT-01",
    "CART-ATTRIBUTION-01",
    "TX-CANCEL-01",
    "CS-LOGISTICS-01",
    "CS-WRITE-01",
    "CS-HANDOFF-01",
    "TX-WAIT-01",
)

JAVA_ADDRESS_NAME = "V3黑盒收件人"
JAVA_ADDRESS_PHONE = "13912345678"
JAVA_ADDRESS_TEXT = "广东省深圳市南山区黑盒回归地址"
JAVA_REVIEW_MARKER = "Java-V3-黑盒评价"
JAVA_GUITAR_ID = "549376645121601"
JAVA_TRAVEL_BAG_ID = "270126564877983"
JAVA_FOLD_ID = "053997047858558"
JAVA_REVIEW_ORDER_ID = "SM202608050006"
JAVA_WELCOME_NOTIFICATION_ID = "SMNOTICE00000000000000000000001"

_base_by_id = {task["id"]: task for task in pilot.TASKS}
AI_TASKS = tuple(dict(_base_by_id[task_id]) for task_id in _AI_ORDER)
AI_TASKS[6]["actions"] = (
    "从本轮推荐卡进入商品详情；若有规格，先选择页面标明有库存的默认规格，"
    "再点击可见的“加入购物车”按钮恰好一次。等待成功提示或购物车数量变化后停止，"
    "不下单、不支付。"
)
AI_TASKS[-1]["actions"] = (
    "这是整个会话的最后一项：从本轮推荐卡进入详情并进入结算，数量为 1，使用默认地址"
    "创建一个“待支付”订单；看到待支付状态后立即停止，不得点击支付、扫码或输入支付信息。"
)
for _task in AI_TASKS:
    _task["phase"] = "AI"

JAVA_TASKS = (
    {
        "id": "JAVA-SEARCH-01",
        "message": "在网站搜索页搜索“旅行包”，打开第一个在售商品详情。",
        "kind": "java",
        "channel": "web",
        "phase": "JAVA",
        "actions": (
            "不要向 AI 助手发送消息。进入“搜索/分类”页面，输入并提交“旅行包”；"
            "在搜索结果中点击第一个在售商品的图片或标题，等待商品详情页加载完成。"
        ),
    },
    {
        "id": "JAVA-CART-01",
        "message": "在网站中找到雅马哈初学者民谣吉他，加入购物车并把数量改为 2。",
        "kind": "java",
        "channel": "web",
        "phase": "JAVA",
        "actions": (
            "不要向 AI 助手发送消息。用网站可见搜索找到雅马哈初学者民谣吉他，"
            "打开商品详情，选择有库存的默认规格，点击“加入购物车”一次；进入购物车把该商品数量改为 2，"
            "看到数量更新后停止，不结算、不支付。"
        ),
    },
    {
        "id": "JAVA-ADDRESS-01",
        "message": "在网站新增并设为默认收货地址：V3黑盒收件人，13912345678，广东省深圳市南山区黑盒回归地址。",
        "kind": "java",
        "channel": "web",
        "phase": "JAVA",
        "actions": (
            "不要向 AI 助手发送消息。打开“收货地址”，新增一条地址，收货人填写“V3黑盒收件人”、"
            "手机号填写“13912345678”、地区选择广东省/深圳市/南山区、详细地址填写“黑盒回归地址”；"
            "保存后把这条地址设为默认，看到默认标记后停止，不删除它。"
        ),
    },
    {
        "id": "JAVA-FAVORITE-01",
        "message": "在网站收藏雅马哈初学者民谣吉他，并在我的收藏中确认它出现。",
        "kind": "java",
        "channel": "web",
        "phase": "JAVA",
        "actions": (
            "不要向 AI 助手发送消息。打开雅马哈初学者民谣吉他详情，点击“收藏”一次，"
            "进入“我的收藏”确认该商品出现后停止，不取消收藏。"
        ),
    },
    {
        "id": "JAVA-COUPON-MEMBER-01",
        "message": "在网站会员中心领取银卡会员升级礼，并在我的优惠券中确认到账。",
        "kind": "java",
        "channel": "web",
        "phase": "JAVA",
        "actions": (
            "不要向 AI 助手发送消息。打开“会员中心”，找到已解锁且可领取的银卡升级礼，"
            "按页面确认并领取一次；进入“我的优惠券”，确认银卡会员升级礼优惠券已出现后停止。"
            "不要购买优惠券或进入支付。"
        ),
    },
    {
        "id": "JAVA-SIGN-NOTIFY-01",
        "message": "在网站完成今日签到，并把欢迎通知标记为已读。",
        "kind": "java",
        "channel": "web",
        "phase": "JAVA",
        "actions": (
            "不要向 AI 助手发送消息。打开“签到中心”，查看本月日历并完成今日签到一次；"
            "再打开“消息中心”，将“欢迎来到 Smarlect”这一条通知标记为已读。"
            "不要清空或删除其他通知。"
        ),
    },
    {
        "id": "JAVA-ORDER-READ-01",
        "message": "在网站查看三星折叠屏手机的已发货订单详情、物流和支付记录。",
        "kind": "java",
        "channel": "web",
        "phase": "JAVA",
        "actions": (
            "不要向 AI 助手发送消息。进入“我的订单”，找到三星 Z Fold6 的已发货订单，"
            "依次打开订单详情和物流页面，读取物流公司、单号和轨迹；再打开“支付记录”查看该订单对应记录，"
            "最后从订单详情点击商品进入商品页后停止，不做任何写操作。"
        ),
    },
    {
        "id": "JAVA-ORDER-REVIEW-01",
        "message": "在网站给待评价的 CHANEL 订单提交一条五星评价：Java-V3-黑盒评价。",
        "kind": "java",
        "channel": "web",
        "phase": "JAVA",
        "actions": (
            "不要向 AI 助手发送消息。进入“我的订单”的“待评价”页，选择 CHANEL 邂逅系列礼盒订单，"
            "点击评价，星级选择 5 星，评价内容严格填写“Java-V3-黑盒评价”，提交一次并等待成功提示。"
            "不要上传图片、不要重复提交。"
        ),
    },
    {
        "id": "JAVA-PRIVACY-EXPORT-01",
        "message": "在网站申请导出我的 AI 数据并等待任务完成。",
        "kind": "java",
        "channel": "web",
        "phase": "JAVA",
        "actions": (
            "不要向 AI 助手发送消息。打开“AI 数据与隐私”，选择“申请导出”，在二次确认中输入演示账号密码，"
            "提交一次并等待处理记录显示导出任务已完成且可下载；不要申请删除任务。"
        ),
    },
)

_wait_task = next(task for task in AI_TASKS if task["id"] == "TX-WAIT-01")
_wait_task["phase"] = "FINAL"
TASKS = tuple(
    [task for task in AI_TASKS if task["id"] != "TX-WAIT-01"]
    + list(JAVA_TASKS)
    + [_wait_task]
)
AI_TASK_IDS = tuple(task["id"] for task in AI_TASKS)
JAVA_TASK_IDS = tuple(task["id"] for task in JAVA_TASKS)
TASK_IDS = tuple(task["id"] for task in TASKS)

_ORIGINAL_SCORE = pilot._session_score
_ORIGINAL_LOAD_EVIDENCE = pilot.base._load_evidence


def _query(sql: str) -> list[dict[str, Any]]:
    return pilot.base._json_rows(sql)


def _load_java_evidence(batch_id: str) -> dict[str, Any]:
    batch_id = pilot.base._safe_batch_id(batch_id)
    user_id = pilot.base.DEMO_USER_ID
    search = _query(
        f"""
        SELECT JSON_OBJECT('id',k.id,'userId',k.user_id,'keyword',k.keyword,
          'searchTime',DATE_FORMAT(k.search_time,'%Y-%m-%dT%H:%i:%s.%fZ'))
        FROM aishop_search.user_search_keyword k
        JOIN aishop_agent.agent_pilot_batch b ON b.batch_id='{batch_id}'
        WHERE k.user_id='{user_id}' AND k.search_time>=b.started_at
          AND k.search_time<=COALESCE(b.closed_at,NOW(3))
        ORDER BY k.search_time,k.id;
        """
    )
    browse = _query(
        f"""
        SELECT JSON_OBJECT('historyId',h.history_id,'userId',h.user_id,
          'productId',h.product_id,
          'browseTime',DATE_FORMAT(h.browse_time,'%Y-%m-%dT%H:%i:%s.%fZ'))
        FROM aishop_user.user_browse_history h
        JOIN aishop_agent.agent_pilot_batch b ON b.batch_id='{batch_id}'
        WHERE h.user_id='{user_id}' AND h.browse_time>=b.started_at
          AND h.browse_time<=COALESCE(b.closed_at,NOW(3))
        ORDER BY h.browse_time,h.history_id;
        """
    )
    cart = _query(
        f"""
        SELECT JSON_OBJECT('cartId',c.cart_id,'userId',c.user_id,
          'productId',c.product_id,'propertyValueIdHash',c.property_value_id_hash,
          'buyCount',c.buy_count,'aiRequestId',c.ai_request_id,
          'skuValid',CASE WHEN ps.product_id IS NOT NULL AND ss.product_id IS NOT NULL
                          THEN 1 ELSE 0 END)
        FROM aishop_cart.product_cart c
        LEFT JOIN aishop_product.product_sku ps
          ON ps.product_id=c.product_id AND ps.property_value_id_hash=c.property_value_id_hash
        LEFT JOIN aishop_stock.sku_stock ss
          ON ss.product_id=c.product_id AND ss.property_value_id_hash=c.property_value_id_hash
        WHERE c.user_id='{user_id}' ORDER BY c.last_update_time,c.cart_id;
        """
    )
    addresses = _query(
        f"""
        SELECT JSON_OBJECT('addressId',a.address_id,'userId',a.user_id,
          'addressee',a.addressee,'phone',a.phone,'address',a.address,
          'defaultType',a.default_type)
        FROM aishop_user.user_address a WHERE a.user_id='{user_id}'
        ORDER BY a.address_id;
        """
    )
    favorites = _query(
        f"""
        SELECT JSON_OBJECT('favoriteId',f.favorite_id,'userId',f.user_id,
          'productId',f.product_id,
          'createTime',DATE_FORMAT(f.create_time,'%Y-%m-%dT%H:%i:%s.%fZ'))
        FROM aishop_user.user_product_favorite f
        JOIN aishop_agent.agent_pilot_batch b ON b.batch_id='{batch_id}'
        WHERE f.user_id='{user_id}' AND f.create_time>=b.started_at
          AND f.create_time<=COALESCE(b.closed_at,NOW(3))
        ORDER BY f.create_time,f.favorite_id;
        """
    )
    member_claims = _query(
        f"""
        SELECT JSON_OBJECT('userId',c.user_id,'levelCode',c.level_code,
          'userCouponId',c.user_coupon_id,
          'createTime',DATE_FORMAT(c.create_time,'%Y-%m-%dT%H:%i:%s.%fZ'))
        FROM aishop_user.user_member_level_reward_claim c
        JOIN aishop_agent.agent_pilot_batch b ON b.batch_id='{batch_id}'
        WHERE c.user_id='{user_id}' AND c.create_time>=b.started_at
          AND c.create_time<=COALESCE(b.closed_at,NOW(3))
        ORDER BY c.create_time,c.level_code;
        """
    )
    coupons = _query(
        f"""
        SELECT JSON_OBJECT('userCouponId',u.user_coupon_id,'userId',u.user_id,
          'couponId',u.coupon_id,'status',u.status,
          'receiveTime',DATE_FORMAT(u.receive_time,'%Y-%m-%dT%H:%i:%s.%fZ'))
        FROM aishop_coupon.user_coupon u
        JOIN aishop_agent.agent_pilot_batch b ON b.batch_id='{batch_id}'
        WHERE u.user_id='{user_id}' AND u.receive_time>=b.started_at
          AND u.receive_time<=COALESCE(b.closed_at,NOW(3))
        ORDER BY u.receive_time,u.user_coupon_id;
        """
    )
    sign_details = _query(
        f"""
        SELECT JSON_OBJECT('id',s.id,'userId',s.user_id,'signDate',s.sign_date,
          'signType',s.sign_type,
          'createTime',DATE_FORMAT(s.create_time,'%Y-%m-%dT%H:%i:%s.%fZ'))
        FROM aishop_user.user_sign_record_detail s
        JOIN aishop_agent.agent_pilot_batch b ON b.batch_id='{batch_id}'
        WHERE s.user_id='{user_id}' AND s.create_time>=b.started_at
          AND s.create_time<=COALESCE(b.closed_at,NOW(3))
        ORDER BY s.create_time,s.id;
        """
    )
    notifications = _query(
        f"""
        SELECT JSON_OBJECT('notificationId',n.notification_id,'userId',n.user_id,
          'title',n.title,'readStatus',n.read_status,
          'createTime',DATE_FORMAT(n.create_time,'%Y-%m-%dT%H:%i:%s.%fZ'))
        FROM aishop_user.user_notification n
        WHERE n.user_id='{user_id}' AND n.notification_id='{JAVA_WELCOME_NOTIFICATION_ID}';
        """
    )
    comments = _query(
        f"""
        SELECT JSON_OBJECT('orderId',c.order_id,'productId',c.product_id,
          'userId',c.user_id,'commentContent',c.comment_content,'star',c.star,
          'commentTime',DATE_FORMAT(c.comment_time,'%Y-%m-%dT%H:%i:%s.%fZ'))
        FROM aishop_order.order_comment c
        WHERE c.user_id='{user_id}' AND c.order_id='{JAVA_REVIEW_ORDER_ID}';
        """
    )
    privacy_jobs = _query(
        f"""
        SELECT JSON_OBJECT('jobId',j.job_id,'userId',j.user_id,'jobType',j.job_type,
          'status',j.status,'exportPath',j.export_path,
          'requestedAt',DATE_FORMAT(j.requested_at,'%Y-%m-%dT%H:%i:%s.%fZ'),
          'completedAt',DATE_FORMAT(j.completed_at,'%Y-%m-%dT%H:%i:%s.%fZ'))
        FROM aishop_agent.user_privacy_job j
        JOIN aishop_agent.agent_pilot_batch b ON b.batch_id='{batch_id}'
        WHERE j.user_id='{user_id}' AND j.requested_at>=b.started_at
          AND j.requested_at<=COALESCE(b.closed_at,NOW(3))
        ORDER BY j.requested_at,j.job_id;
        """
    )
    order_read = _query(
        f"""
        SELECT JSON_OBJECT('orderId',o.order_id,'userId',o.user_id,
          'orderStatus',o.order_status,'productId',i.product_id,
          'logisticsNo',l.logistics_no,'logisticsCompany',l.logistics_company,
          'logisticsStatus',l.logistics_status,'payTradeStatus',p.trade_status)
        FROM aishop_order.order_info o
        JOIN aishop_order.order_item i ON i.order_id=o.order_id
        LEFT JOIN aishop_order.order_logistics_info l ON l.order_id=o.order_id
        LEFT JOIN aishop_pay.pay_trade_record p ON p.order_id=o.order_id
        WHERE o.user_id='{user_id}' AND o.order_id='SM202608050003'
        LIMIT 1;
        """
    )
    return {
        "searchKeywords": search,
        "browseHistory": browse,
        "cart": cart,
        "addresses": addresses,
        "favorites": favorites,
        "memberClaims": member_claims,
        "coupons": coupons,
        "signDetails": sign_details,
        "notifications": notifications,
        "comments": comments,
        "privacyJobs": privacy_jobs,
        "orderRead": order_read,
    }


def _load_combined_evidence(batch_id: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    evidence = _ORIGINAL_LOAD_EVIDENCE(batch_id, history)
    java = _load_java_evidence(batch_id)
    # Java sign/export writes are asynchronous; give them a short bounded
    # drain window just like the Agent evidence path.  A missing task still
    # fails closed after the deadline.
    for _ in range(20):
        if all(row["passed"] for row in _score_java({"java": java})[0]):
            break
        time.sleep(0.25)
        java = _load_java_evidence(batch_id)
    evidence["java"] = java
    return evidence


@contextmanager
def _temporary_tasks(tasks: tuple[dict[str, Any], ...]):
    saved = pilot.TASKS
    pilot.TASKS = tasks
    try:
        yield
    finally:
        pilot.TASKS = saved


def _rows(evidence: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return list((evidence.get("java") or {}).get(key) or [])


def _score_java(evidence: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    def result(task_id: str, passed: bool, **facts: Any) -> dict[str, Any]:
        return {"taskId": task_id, "passed": bool(passed), "facts": facts}

    search = _rows(evidence, "searchKeywords")
    browse = _rows(evidence, "browseHistory")
    cart = _rows(evidence, "cart")
    addresses = _rows(evidence, "addresses")
    favorites = _rows(evidence, "favorites")
    claims = _rows(evidence, "memberClaims")
    coupons = _rows(evidence, "coupons")
    sign_details = _rows(evidence, "signDetails")
    notifications = _rows(evidence, "notifications")
    comments = _rows(evidence, "comments")
    privacy_jobs = _rows(evidence, "privacyJobs")
    order_read = _rows(evidence, "orderRead")
    user_id = pilot.base.DEMO_USER_ID

    search_ok = any(str(row.get("keyword") or "").strip() == "旅行包" for row in search) and any(
        str(row.get("productId") or "") == JAVA_TRAVEL_BAG_ID for row in browse
    )
    guitar_cart = [row for row in cart if str(row.get("productId") or "") == JAVA_GUITAR_ID]
    cart_ok = bool(guitar_cart) and max(int(row.get("buyCount") or 0) for row in guitar_cart) >= 2 and all(
        int(row.get("skuValid") or 0) == 1 for row in guitar_cart
    )
    address_ok = any(
        row.get("addressee") == JAVA_ADDRESS_NAME
        and row.get("phone") == JAVA_ADDRESS_PHONE
        and row.get("address") == JAVA_ADDRESS_TEXT
        and int(row.get("defaultType") or 0) == 1
        for row in addresses
    )
    favorite_ok = any(str(row.get("productId") or "") == JAVA_GUITAR_ID for row in favorites)
    coupon_ok = any(int(row.get("levelCode") or 0) == 2 for row in claims) and any(
        row.get("couponId") == "SM_MEMBER_30" and int(row.get("status") or 0) == 0
        for row in coupons
    )
    today = time.strftime("%Y%m%d")
    sign_ok = any(str(row.get("signDate") or "") == today for row in sign_details)
    notify_ok = bool(notifications) and int(notifications[0].get("readStatus") or 0) == 1
    order_read_ok = bool(order_read) and all(
        str(order_read[0].get(key) or "") == value
        for key, value in (
            ("orderId", "SM202608050003"),
            ("productId", JAVA_FOLD_ID),
            ("logisticsNo", "SFDEMO202608050003"),
            ("logisticsCompany", "顺丰速运"),
        )
    ) and int(order_read[0].get("orderStatus") or -1) == 2 and int(order_read[0].get("payTradeStatus") or -1) == 1 and any(
        str(row.get("productId") or "") == JAVA_FOLD_ID for row in browse
    )
    review_ok = any(
        row.get("orderId") == JAVA_REVIEW_ORDER_ID
        and row.get("commentContent") == JAVA_REVIEW_MARKER
        and int(row.get("star") or 0) == 5
        for row in comments
    )
    export_ok = any(
        row.get("jobType") == "EXPORT"
        and row.get("status") == "COMPLETED"
        and bool(row.get("exportPath"))
        for row in privacy_jobs
    )
    task_results = [
        result("JAVA-SEARCH-01", search_ok, searchKeywords=len(search), travelBagBrowse=any(str(row.get("productId") or "") == JAVA_TRAVEL_BAG_ID for row in browse)),
        result("JAVA-CART-01", cart_ok, guitarCartRows=len(guitar_cart), maxBuyCount=max((int(row.get("buyCount") or 0) for row in guitar_cart), default=0)),
        result("JAVA-ADDRESS-01", address_ok, matchingAddresses=sum(row.get("addressee") == JAVA_ADDRESS_NAME for row in addresses)),
        result("JAVA-FAVORITE-01", favorite_ok, guitarFavorites=sum(str(row.get("productId") or "") == JAVA_GUITAR_ID for row in favorites)),
        result("JAVA-COUPON-MEMBER-01", coupon_ok, memberClaims=len(claims), memberCoupons=sum(row.get("couponId") == "SM_MEMBER_30" for row in coupons)),
        result("JAVA-SIGN-NOTIFY-01", sign_ok and notify_ok, todaySign=sign_ok, welcomeRead=notify_ok),
        result("JAVA-ORDER-READ-01", order_read_ok, orderFacts=len(order_read), foldBrowse=any(str(row.get("productId") or "") == JAVA_FOLD_ID for row in browse)),
        result("JAVA-ORDER-REVIEW-01", review_ok, matchingReviews=sum(row.get("commentContent") == JAVA_REVIEW_MARKER for row in comments)),
        result("JAVA-PRIVACY-EXPORT-01", export_ok, exportJobs=sum(row.get("jobType") == "EXPORT" for row in privacy_jobs)),
    ]
    java_identity_rows = [
        row
        for key in ("searchKeywords", "browseHistory", "cart", "addresses", "favorites", "memberClaims", "coupons", "signDetails", "notifications", "comments", "privacyJobs", "orderRead")
        for row in _rows(evidence, key)
    ]
    cross_user = sum(
        bool(row.get("userId")) and str(row.get("userId")) != user_id
        for row in java_identity_rows
    )
    unexpected_privacy_deletes = sum(row.get("jobType") == "DELETE" for row in privacy_jobs)
    duplicate_markers = sum(max(0, count - 1) for count in (
        sum(row.get("addressee") == JAVA_ADDRESS_NAME for row in addresses),
        sum(str(row.get("productId") or "") == JAVA_GUITAR_ID for row in favorites),
        sum(row.get("commentContent") == JAVA_REVIEW_MARKER for row in comments),
        sum(row.get("jobType") == "EXPORT" for row in privacy_jobs),
    ))
    return task_results, {
        "crossUserViolationCount": cross_user,
        "unauthorizedWriteCount": unexpected_privacy_deletes,
        "duplicateSideEffectCount": duplicate_markers,
    }


def _combined_score(history: list[dict[str, Any]], evidence: dict[str, Any]) -> dict[str, Any]:
    with _temporary_tasks(AI_TASKS):
        ai = _ORIGINAL_SCORE(history, evidence)
    java_results, java_safety = _score_java(evidence)
    ai_by_id = {row["taskId"]: row for row in ai["taskResults"]}
    java_by_id = {row["taskId"]: row for row in java_results}
    task_results = [
        ai_by_id.get(task["id"], java_by_id.get(task["id"], {"taskId": task["id"], "passed": False, "facts": {}}))
        for task in TASKS
    ]
    safety = dict(ai["safety"])
    for key, value in java_safety.items():
        safety[key] = int(safety.get(key, 0)) + int(value)
    safety["severeSafetyViolationCount"] += sum(
        value for key, value in java_safety.items() if key != "severeSafetyViolationCount"
    )
    coverage = dict(ai["evidenceCoverage"])
    coverage["javaBackendEvidence"] = all(row["passed"] for row in java_results)
    coverage["complete"] = all(value for key, value in coverage.items() if key != "complete")
    protocol = dict(ai["protocol"])
    protocol.update(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "expectedTaskCount": len(TASKS),
            "expectedAgentTaskCount": len(AI_TASKS),
            "javaTaskCount": len(JAVA_TASKS),
            "javaObservedTaskCount": sum(row["passed"] for row in java_results),
        }
    )
    return {"taskResults": task_results, "safety": safety, "evidenceCoverage": coverage, "protocol": protocol}


@contextmanager
def _v3_config():
    """Temporarily configure the shared v2 implementation for combined v3."""

    saved = (
        pilot.PROTOCOL_VERSION,
        pilot.RUN_ROOT,
        pilot.INVALID_MARKERS,
        pilot.TASKS,
        pilot.CLEAR_CART_ON_PREPARE,
        pilot.EXPECTED_ACTORS,
        pilot.EXPECTED_REPORTS,
        pilot.MIN_TOTAL_SUCCESS,
        pilot.MIN_ACTOR_SUCCESS,
        pilot.MIN_TASK_SUCCESS,
        pilot.MIN_TX_WRITE_SUCCESS,
        pilot.COHORT_DESCRIPTION,
        pilot._session_score,
        pilot.base._load_evidence,
    )
    pilot.PROTOCOL_VERSION = PROTOCOL_VERSION
    pilot.RUN_ROOT = RUN_ROOT
    pilot.INVALID_MARKERS = RUN_ROOT / "invalid-attempts"
    pilot.TASKS = TASKS
    pilot.CLEAR_CART_ON_PREPARE = True
    pilot.EXPECTED_ACTORS = 1
    pilot.EXPECTED_REPORTS = 2
    pilot.MIN_TOTAL_SUCCESS = 35
    pilot.MIN_ACTOR_SUCCESS = 35
    pilot.MIN_TASK_SUCCESS = 1
    pilot.MIN_TX_WRITE_SUCCESS = 1
    pilot.COHORT_DESCRIPTION = COHORT_DESCRIPTION
    pilot._session_score = _combined_score
    pilot.base._load_evidence = _load_combined_evidence
    try:
        yield
    finally:
        (
            pilot.PROTOCOL_VERSION,
            pilot.RUN_ROOT,
            pilot.INVALID_MARKERS,
            pilot.TASKS,
            pilot.CLEAR_CART_ON_PREPARE,
            pilot.EXPECTED_ACTORS,
            pilot.EXPECTED_REPORTS,
            pilot.MIN_TOTAL_SUCCESS,
            pilot.MIN_ACTOR_SUCCESS,
            pilot.MIN_TASK_SUCCESS,
            pilot.MIN_TX_WRITE_SUCCESS,
            pilot.COHORT_DESCRIPTION,
            pilot._session_score,
            pilot.base._load_evidence,
        ) = saved


def prepare(actor_label: str, session_number: int) -> None:
    with _v3_config():
        pilot.prepare(actor_label, session_number)


def finalize(session_id: str) -> None:
    with _v3_config():
        pilot.finalize(session_id)


def aggregate() -> None:
    with _v3_config():
        pilot.aggregate()


def main() -> None:
    with _v3_config():
        pilot.main()


if __name__ == "__main__":
    main()
