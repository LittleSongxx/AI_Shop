from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import structlog

from app.constants import (
    CONFIRM_RECEIPT_ORDER_STATUSES,
    ORDER_ITEM_STATUS_NORMAL,
    ORDER_STATUS_COMPLETED,
    ORDER_STATUS_NAMES,
    ORDER_STATUS_PAID,
    ORDER_STATUS_PARTIALLY_REFUNDED,
    ORDER_STATUS_REFUNDED,
    ORDER_STATUS_SHIPPED,
    ORDER_STATUS_WAIT_PAYMENT,
    REFUNDABLE_ORDER_STATUSES,
)
from app.domain.intent.types import IntentKind
from app.harness.metrics.runtime_sensors import (
    ORDER_REFERENCE_LATENCY,
    ORDER_REFERENCE_TOTAL,
)
from app.services.java_internal_client import java_internal_client
from app.services.product_search_query import topic_terms_for_text
from app.utils.order_ids import extract_order_id, extract_order_item_id

logger = structlog.get_logger()


class OrderReferenceOutcome(str, Enum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    NO_MATCH = "NO_MATCH"
    NO_ELIGIBLE = "NO_ELIGIBLE"
    INVALID_SELECTION = "INVALID_SELECTION"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


ORDER_REFERENCE_INTENTS = frozenset(
    {
        IntentKind.QUERY_ORDER.value,
        IntentKind.REFUND.value,
        IntentKind.CANCEL_ORDER.value,
        IntentKind.CONFIRM_RECEIPT.value,
        IntentKind.QUERY_LOGISTICS.value,
        IntentKind.QUERY_FULFILLMENT.value,
        IntentKind.PRODUCT_REVIEW.value,
        IntentKind.RECOMMENT.value,
        IntentKind.QUERY_COMMENT.value,
        IntentKind.REFUND_STATUS.value,
        IntentKind.ADDRESS_CHANGE.value,
        IntentKind.INVOICE.value,
        IntentKind.DAMAGED_OR_WRONG_ITEM.value,
        IntentKind.AFTERSALES_UNKNOWN.value,
    }
)

_ITEM_TARGET_INTENTS = frozenset(
    {
        IntentKind.REFUND.value,
        IntentKind.REFUND_STATUS.value,
        IntentKind.DAMAGED_OR_WRONG_ITEM.value,
        IntentKind.AFTERSALES_UNKNOWN.value,
    }
)

_STATUS_HINTS: tuple[tuple[tuple[str, ...], frozenset[int]], ...] = (
    (("待付款", "没付款", "未付款"), frozenset({ORDER_STATUS_WAIT_PAYMENT})),
    (("待发货", "没发货", "未发货", "还没发", "未出库"), frozenset({ORDER_STATUS_PAID})),
    (("已发货", "发货了", "在路上", "运输中"), frozenset({ORDER_STATUS_SHIPPED})),
    (
        ("刚收到", "收到的", "收到了", "收到后"),
        frozenset(
            {
                ORDER_STATUS_SHIPPED,
                ORDER_STATUS_COMPLETED,
                ORDER_STATUS_PARTIALLY_REFUNDED,
            }
        ),
    ),
    (("已完成", "已收货"), frozenset({ORDER_STATUS_COMPLETED})),
    (("已退款", "退款了"), frozenset({ORDER_STATUS_REFUNDED, ORDER_STATUS_PARTIALLY_REFUNDED})),
)

_RECENT_HINTS = ("最近", "上次", "刚买", "刚下单", "最新")
_SEVEN_DAY_HINTS = ("前几天", "这几天", "近几天")
_NON_PRODUCT_CLUE_PHRASES = (
    "请选择", "原诉求", "继续处理", "查询退款进度", "确认收货", "查看评价",
    "查询评价", "修改收货地址", "处理地址问题", "处理发票问题", "处理商品问题",
    "怎么还没发货", "催一下发货", "待发货", "没发货", "未发货", "已发货",
    "待付款", "没付款", "未付款",
    "退款进度", "申请退款", "退款", "退货", "退钱", "取消订单", "取消",
    "评价一下", "写评价", "评价", "追评", "查询物流", "物流", "快递", "包裹",
    "发货状态", "发货", "收货地址", "改地址", "地址", "开发票", "发票",
    "破损", "损坏", "坏了", "错发", "发错", "漏发", "少发", "缺件",
    "我收到的", "收到的", "收到了", "收到", "我买的", "购买的", "买的",
    "最近", "上次", "刚买", "刚下单", "昨天", "前几天", "这几天",
    "再买一次", "再买", "复购", "订单号", "订单项", "订单", "商品",
    "我选择了", "选择", "我要", "我想", "我的", "帮我", "给我", "请",
    "查一下", "查查", "查询", "查看", "看看", "这个", "那个", "这单", "那单", "这件",
    "那件", "这款", "它", "东西", "目标", "到哪了", "进度", "状态",
    "怎么", "为什么", "一般", "通常", "多久", "办理", "一下",
    # Proposal wording and quantity/filler terms are not product identity.
    # Without these stops, "最近一笔待付款订单，请给出确认提案" leaves
    # tokens such as "一笔" and "给出" and incorrectly filters a valid order.
    "确认提案", "确认卡片", "确认卡", "操作确认", "生成提案", "生成方案",
    "展示方案", "给出", "提案", "方案", "等待我确认", "等我确认", "只生成",
    "仅生成", "只做预览", "仅做预览", "当前只做预览", "不要实际执行",
    "不要写入", "不写入", "保留提案", "远程结果未知", "伪造成功", "实际执行",
    "一笔", "一单", "一件",
)


@dataclass
class OrderReferenceResolution:
    outcome: OrderReferenceOutcome
    intent: str
    target: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    matched_candidates: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    clues: dict[str, Any] = field(default_factory=dict)


class OrderReferenceResolver:
    LOOKBACK_DAYS = 90
    ORDER_LIMIT = 30
    CARD_LIMIT = 5

    async def resolve(
        self,
        *,
        user_id: str,
        intent: str,
        user_text: str,
        entities: dict[str, str] | None = None,
        consult_card: dict[str, Any] | None = None,
        pending_reference: dict[str, Any] | None = None,
        enforce_action_eligibility: bool = True,
    ) -> OrderReferenceResolution:
        started = time.perf_counter()
        try:
            resolution = await self._resolve(
                user_id=user_id,
                intent=intent,
                user_text=user_text,
                entities=entities or {},
                consult_card=consult_card,
                pending_reference=pending_reference or {},
                enforce_action_eligibility=enforce_action_eligibility,
            )
        except Exception as exc:
            logger.exception(
                "order_reference_dependency_failed",
                intent=intent,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            resolution = OrderReferenceResolution(
                outcome=OrderReferenceOutcome.DEPENDENCY_ERROR,
                intent=intent,
                reason="订单服务暂时不可用，请稍后重试，或回复“转人工”继续处理。",
            )
        finally:
            ORDER_REFERENCE_LATENCY.observe(time.perf_counter() - started)
        ORDER_REFERENCE_TOTAL.labels(
            intent=intent or "UNKNOWN", outcome=resolution.outcome.value.lower()
        ).inc()
        return resolution

    async def _resolve(
        self,
        *,
        user_id: str,
        intent: str,
        user_text: str,
        entities: dict[str, str],
        consult_card: dict[str, Any] | None,
        pending_reference: dict[str, Any],
        enforce_action_eligibility: bool,
    ) -> OrderReferenceResolution:
        pending_valid = self._pending_reference_valid(pending_reference, intent)
        pending_item = pending_reference.get("orderItemId") if pending_valid else None
        pending_order = pending_reference.get("orderId") if pending_valid else None
        explicit_item_id = extract_order_item_id(
            user_text, entities.get("orderItemId"), pending_item
        )
        explicit_order_id = extract_order_id(
            user_text,
            entities.get("orderId"),
            explicit_item_id,
            pending_order,
        )
        end = datetime.now()
        start = end - timedelta(days=self.LOOKBACK_DAYS)
        orders = await java_internal_client.list_orders(
            user_id,
            order_id=explicit_order_id,
            time_start=None if explicit_order_id else start.strftime("%Y-%m-%d 00:00:00"),
            time_end=None if explicit_order_id else end.strftime("%Y-%m-%d %H:%M:%S"),
            limit=self.ORDER_LIMIT,
        )
        orders = [self._normalize_order(row) for row in orders if isinstance(row, dict)]
        all_candidates = self._flatten(orders, intent)
        clues: dict[str, Any] = {
            "explicitOrderId": explicit_order_id,
            "explicitOrderItemId": explicit_item_id,
        }

        if explicit_order_id and not orders:
            return OrderReferenceResolution(
                outcome=OrderReferenceOutcome.NO_MATCH,
                intent=intent,
                reason="没有在你的订单中找到该订单号，请核对后重试。",
                clues=clues,
            )

        eligible = [
            row
            for row in all_candidates
            if not enforce_action_eligibility or self._is_eligible(row, intent)
        ]
        working = list(eligible)

        if not eligible and all_candidates:
            ineligible_matches = list(all_candidates)
            consult_product_id = str(
                (consult_card or {}).get("productId")
                or (consult_card or {}).get("product_id")
                or ""
            ).strip()
            if consult_product_id:
                ineligible_matches = [
                    row
                    for row in ineligible_matches
                    if row.get("productId") == consult_product_id
                ]
            ineligible_terms = topic_terms_for_text(user_text)
            if ineligible_terms:
                ineligible_matches = [
                    row
                    for row in ineligible_matches
                    if self._candidate_matches_terms(row, ineligible_terms)
                ]
            if ineligible_matches:
                return self._no_eligible(intent, ineligible_matches, clues)
            return self._no_match(intent, [], clues, user_text)

        if explicit_item_id:
            working = [
                row for row in working if row.get("orderItemId") == explicit_item_id
            ]
            all_explicit = [
                row for row in all_candidates if row.get("orderItemId") == explicit_item_id
            ]
            if not working and all_explicit:
                return self._no_eligible(intent, all_explicit, clues)

        status_filter = self._status_filter(user_text)
        if status_filter:
            clues["statuses"] = sorted(status_filter)
            working = [row for row in working if row.get("orderStatus") in status_filter]
            if not working:
                status_matches = [
                    row for row in all_candidates if row.get("orderStatus") in status_filter
                ]
                if status_matches:
                    return self._no_eligible(intent, status_matches, clues)
                return self._no_match(intent, eligible, clues, user_text)

        working, has_time_clue = self._apply_time_filter(working, user_text)
        if has_time_clue:
            clues["time"] = True
        if not working:
            return self._no_match(intent, eligible, clues, user_text)

        topic_terms = topic_terms_for_text(user_text)
        explicit_topic_terms = sorted(
            {
                term
                for term in topic_terms
                if str(term).lower() in user_text.lower()
            },
            key=len,
            reverse=True,
        )
        lexical_clues = (
            []
            if explicit_order_id or explicit_item_id
            else self._lexical_product_clues(user_text, topic_terms)
        )
        if lexical_clues:
            clues["productClues"] = lexical_clues[:8]

        consult_product_id = str(
            (consult_card or {}).get("productId")
            or (consult_card or {}).get("product_id")
            or ""
        ).strip()
        use_consult_product = bool(consult_product_id) and (
            not topic_terms and not lexical_clues
            or self._has_product_pronoun(user_text) and not lexical_clues
        )
        if use_consult_product:
            product_matches = [
                row for row in working if row.get("productId") == consult_product_id
            ]
            if product_matches:
                working = product_matches
                clues["productId"] = consult_product_id
            elif self._has_product_pronoun(user_text):
                return self._no_match(intent, eligible, clues, user_text)

        if topic_terms:
            product_matches = [
                row for row in working if self._candidate_matches_terms(row, topic_terms)
            ]
            clues["productTerms"] = topic_terms[:8]
            if not product_matches:
                return self._no_match(intent, eligible, clues, user_text)
            working = product_matches
            matched_explicit_terms = [
                term
                for term in explicit_topic_terms
                if any(self._candidate_contains_term(row, term) for row in working)
            ]
            if matched_explicit_terms:
                exact_matches = [
                    row
                    for row in working
                    if all(
                        self._candidate_contains_term(row, term)
                        for term in matched_explicit_terms
                    )
                ]
                if exact_matches:
                    working = exact_matches
                    clues["explicitProductTerms"] = matched_explicit_terms[:8]

        if lexical_clues:
            product_matches = [
                row
                for row in working
                if self._candidate_matches_lexical_clues(row, lexical_clues)
            ]
            if not product_matches:
                return self._no_match(intent, eligible, clues, user_text)
            working = product_matches

        if any(hint in user_text for hint in _RECENT_HINTS) and len(working) > 1:
            newest = max(self._order_time(row) for row in working)
            working = [row for row in working if self._order_time(row) == newest]
            clues["newest"] = True

        working = self._deduplicate(working, intent)
        if len(working) == 1:
            return OrderReferenceResolution(
                outcome=OrderReferenceOutcome.RESOLVED,
                intent=intent,
                target=working[0],
                matched_candidates=working,
                clues=clues,
            )
        if len(working) > 1:
            return OrderReferenceResolution(
                outcome=OrderReferenceOutcome.AMBIGUOUS,
                intent=intent,
                candidates=working[: self.CARD_LIMIT],
                matched_candidates=working,
                reason=self._selection_prompt(intent, exact=True),
                clues=clues,
            )
        if all_candidates and not eligible:
            return self._no_eligible(intent, all_candidates, clues)
        return self._no_match(intent, eligible, clues, user_text)

    def _normalize_order(self, order: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(order)
        normalized["order_id"] = str(order.get("order_id") or order.get("orderId") or "")
        normalized["order_status"] = self._int(order.get("order_status", order.get("orderStatus")))
        normalized["comment_status"] = self._int(
            order.get("comment_status", order.get("commentStatus"))
        )
        normalized["items"] = list(order.get("items") or order.get("order_item_list") or [])
        return normalized

    def _flatten(self, orders: list[dict[str, Any]], intent: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        item_target = intent in _ITEM_TARGET_INTENTS
        for order in orders:
            items = order.get("items") or []
            if item_target and items:
                for item in items:
                    rows.append(self._candidate(order, item, target_type="ORDER_ITEM"))
            else:
                representative = items[0] if len(items) == 1 else None
                rows.append(self._candidate(order, representative, target_type="ORDER"))
        return sorted(rows, key=self._sort_key, reverse=True)

    def _candidate(
        self,
        order: dict[str, Any],
        item: dict[str, Any] | None,
        *,
        target_type: str,
    ) -> dict[str, Any]:
        item = item or {}
        order_id = str(order.get("order_id") or "")
        item_id = str(item.get("order_item_id") or item.get("orderItemId") or "")
        target_id = item_id if target_type == "ORDER_ITEM" else order_id
        status = self._int(order.get("order_status"))
        cover = str(item.get("cover") or "").split(",")[0] or None
        search_text = " ".join(
            f"{row.get('product_name') or row.get('productName') or ''} "
            f"{row.get('property_info') or row.get('propertyInfo') or ''} "
            f"{row.get('brand_name') or row.get('brandName') or row.get('brand') or ''} "
            f"{row.get('model') or row.get('model_name') or row.get('modelName') or ''}"
            for row in order.get("items") or []
            if isinstance(row, dict)
        )
        return {
            "targetType": target_type,
            "targetId": target_id,
            "orderId": order_id,
            "orderItemId": item_id or None,
            "productId": str(item.get("product_id") or item.get("productId") or "") or None,
            "productName": item.get("product_name") or item.get("productName") or order.get("subject"),
            "propertyInfo": item.get("property_info") or item.get("propertyInfo"),
            "cover": cover,
            "amount": self._number(item.get("item_amount", order.get("amount"))),
            "orderStatus": status,
            "orderStatusName": ORDER_STATUS_NAMES.get(status, "订单"),
            "orderTime": str(order.get("order_time") or order.get("orderTime") or "") or None,
            "orderItemStatus": self._int(
                item.get("order_item_status", item.get("orderItemStatus"))
            ),
            "commentStatus": self._int(order.get("comment_status")),
            "_searchText": search_text,
        }

    def _is_eligible(self, row: dict[str, Any], intent: str) -> bool:
        status = row.get("orderStatus")
        if intent == IntentKind.REFUND.value:
            return (
                status in REFUNDABLE_ORDER_STATUSES
                and row.get("orderItemStatus") == ORDER_ITEM_STATUS_NORMAL
            )
        if intent == IntentKind.CONFIRM_RECEIPT.value:
            return status in CONFIRM_RECEIPT_ORDER_STATUSES
        if intent == IntentKind.CANCEL_ORDER.value:
            return status == ORDER_STATUS_WAIT_PAYMENT
        if intent == IntentKind.PRODUCT_REVIEW.value:
            return status == ORDER_STATUS_COMPLETED and row.get("commentStatus") == 0
        if intent == IntentKind.RECOMMENT.value:
            return status == ORDER_STATUS_COMPLETED and row.get("commentStatus") == 1
        if intent == IntentKind.QUERY_COMMENT.value:
            return row.get("commentStatus") in {1, 2}
        if intent == IntentKind.DAMAGED_OR_WRONG_ITEM.value:
            return status in {
                ORDER_STATUS_SHIPPED,
                ORDER_STATUS_COMPLETED,
                ORDER_STATUS_PARTIALLY_REFUNDED,
            }
        return True

    def _status_filter(self, text: str) -> frozenset[int] | None:
        for hints, statuses in _STATUS_HINTS:
            if any(hint in text for hint in hints):
                return statuses
        return None

    def _apply_time_filter(
        self, rows: list[dict[str, Any]], text: str
    ) -> tuple[list[dict[str, Any]], bool]:
        now = datetime.now()
        if "昨天" in text:
            day = (now - timedelta(days=1)).date()
            return [row for row in rows if self._order_time(row).date() == day], True
        if any(hint in text for hint in _SEVEN_DAY_HINTS):
            threshold = now - timedelta(days=7)
            return [row for row in rows if self._order_time(row) >= threshold], True
        return rows, any(hint in text for hint in _RECENT_HINTS)

    def _no_match(
        self,
        intent: str,
        eligible: list[dict[str, Any]],
        clues: dict[str, Any],
        user_text: str,
    ) -> OrderReferenceResolution:
        suggestions = self._deduplicate(eligible, intent)[: self.CARD_LIMIT]
        detail = ""
        if clues.get("productClues"):
            shown = "、".join(str(value) for value in clues["productClues"][:3])
            detail = f"与“{shown}”商品描述匹配的"
        elif clues.get("productTerms"):
            detail = "与商品品类匹配的"
        elif clues.get("statuses"):
            detail = "与订单状态匹配的"
        reason = f"我没有在你的订单中找到{detail}目标。"
        if suggestions:
            reason += "以下是最近可办理的订单，请选择一项继续。"
        else:
            reason += "请补充商品名、购买时间或订单号。"
        return OrderReferenceResolution(
            outcome=OrderReferenceOutcome.NO_MATCH,
            intent=intent,
            candidates=suggestions,
            reason=reason,
            clues=clues,
        )

    def _no_eligible(
        self,
        intent: str,
        rows: list[dict[str, Any]],
        clues: dict[str, Any],
    ) -> OrderReferenceResolution:
        rows = self._deduplicate(rows, intent)
        first = rows[0] if rows else {}
        status = first.get("orderStatusName") or "当前状态"
        action = {
            IntentKind.REFUND.value: "申请退款",
            IntentKind.CONFIRM_RECEIPT.value: "确认收货",
            IntentKind.CANCEL_ORDER.value: "取消订单",
            IntentKind.PRODUCT_REVIEW.value: "首次评价",
            IntentKind.RECOMMENT.value: "追评",
            IntentKind.QUERY_COMMENT.value: "查看评价",
        }.get(intent, "办理该操作")
        return OrderReferenceResolution(
            outcome=OrderReferenceOutcome.NO_ELIGIBLE,
            intent=intent,
            candidates=rows[: self.CARD_LIMIT],
            matched_candidates=rows,
            reason=f"找到了相关订单，但订单状态为“{status}”，当前不能{action}。",
            clues=clues,
        )

    def _deduplicate(
        self, rows: list[dict[str, Any]], intent: str
    ) -> list[dict[str, Any]]:
        key_name = "targetId" if intent in _ITEM_TARGET_INTENTS else "orderId"
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in sorted(rows, key=self._sort_key, reverse=True):
            key = str(row.get(key_name) or "")
            if key and key not in seen:
                seen.add(key)
                result.append(row)
        return result

    @staticmethod
    def _candidate_matches_terms(row: dict[str, Any], terms: list[str]) -> bool:
        haystack = " ".join(
            str(row.get(key) or "").lower()
            for key in ("productName", "propertyInfo", "_searchText")
        )
        return any(term in haystack for term in terms)

    @staticmethod
    def _candidate_matches_lexical_clues(
        row: dict[str, Any], clues: list[str]
    ) -> bool:
        haystack = " ".join(
            str(row.get(key) or "").lower()
            for key in ("productName", "propertyInfo", "_searchText")
        )
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", haystack)
        return any(clue in compact for clue in clues)

    @staticmethod
    def _candidate_contains_term(row: dict[str, Any], term: str) -> bool:
        haystack = " ".join(
            str(row.get(key) or "").lower()
            for key in ("productName", "propertyInfo", "_searchText")
        )
        normalized_term = re.sub(
            r"[^a-z0-9\u4e00-\u9fff]+", "", str(term).lower()
        )
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", haystack)
        return bool(normalized_term and normalized_term in compact)

    @staticmethod
    def _lexical_product_clues(text: str, topic_terms: list[str]) -> list[str]:
        cleaned = str(text or "").lower()
        for phrase in sorted(_NON_PRODUCT_CLUE_PHRASES, key=len, reverse=True):
            cleaned = cleaned.replace(phrase, " ")
        for term in sorted(topic_terms, key=len, reverse=True):
            if term in cleaned:
                cleaned = cleaned.replace(term, " ")
        cleaned = re.sub(r"[的了啊呀吗呢吧和与及把将]", " ", cleaned)

        values: list[str] = []
        seen: set[str] = set()

        def add(value: str) -> None:
            compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())
            if len(compact) >= 2 and compact not in seen:
                seen.add(compact)
                values.append(compact)

        for token in re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", cleaned):
            add(token)
        for token in re.findall(r"[\u4e00-\u9fff]{2,}", cleaned):
            add(token)
            if len(token) > 2:
                for index in range(len(token) - 1):
                    add(token[index : index + 2])
        return values

    @staticmethod
    def _has_product_pronoun(text: str) -> bool:
        return any(word in text for word in ("这个", "这件", "这款", "它", "那个", "那件"))

    @staticmethod
    def _selection_prompt(intent: str, *, exact: bool) -> str:
        labels = {
            IntentKind.REFUND.value: "退款",
            IntentKind.CONFIRM_RECEIPT.value: "确认收货",
            IntentKind.CANCEL_ORDER.value: "取消",
            IntentKind.QUERY_LOGISTICS.value: "查询物流",
            IntentKind.QUERY_FULFILLMENT.value: "查询发货状态",
            IntentKind.PRODUCT_REVIEW.value: "评价",
            IntentKind.RECOMMENT.value: "追评",
            IntentKind.QUERY_COMMENT.value: "查看评价",
            IntentKind.REFUND_STATUS.value: "查询退款进度",
        }
        label = labels.get(intent, "继续办理")
        prefix = "找到了多个可能的订单" if exact else "以下是最近可办理的订单"
        return f"{prefix}，请选择要{label}的商品。"

    @staticmethod
    def _order_time(row: dict[str, Any]) -> datetime:
        value = row.get("orderTime")
        if isinstance(value, datetime):
            return value
        text = str(value or "")[:26]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return datetime.min

    def _sort_key(self, row: dict[str, Any]) -> tuple[datetime, str]:
        return self._order_time(row), str(row.get("targetId") or "")

    @staticmethod
    def _pending_reference_valid(reference: dict[str, Any], intent: str) -> bool:
        if not reference or reference.get("intent") != intent:
            return False
        raw = str(reference.get("expiresAt") or "")
        try:
            expires_at = datetime.fromisoformat(raw)
        except ValueError:
            return False
        return expires_at > datetime.now()

    @staticmethod
    def _int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None


order_reference_resolver = OrderReferenceResolver()
