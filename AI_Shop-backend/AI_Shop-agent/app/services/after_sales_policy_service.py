"""Deterministic after-sales eligibility over published, versioned rules.

RAG can explain a policy, but it is not allowed to decide whether a specific
order is eligible. This service owns that decision and only consumes
server-verified order/product facts from Java.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import structlog

from app.config.settings import get_settings
from app.db.pool import acquire
from app.services.java_internal_client import java_internal_client

logger = structlog.get_logger()

ELIGIBLE = "ELIGIBLE"
INELIGIBLE = "INELIGIBLE"
NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
POLICY_UNAVAILABLE = "POLICY_UNAVAILABLE"
CONFLICT = "CONFLICT"
SUPPORTED_ACTIONS = frozenset({"REFUND", "RETURN"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _same(value: Any, expected: Any) -> bool:
    return _text(value).lower() == _text(expected).lower()


def _in(values: Any, value: Any) -> bool:
    if values is None:
        return True
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    return any(_same(item, value) for item in values)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decision_id(*parts: object) -> str:
    digest = hashlib.sha256("\0".join(map(str, parts)).encode()).hexdigest()[:48]
    return f"after_sales_{digest}"


class AfterSalesPolicyService:
    async def evaluate(
        self,
        *,
        user_id: str,
        action: str,
        order_id: str | None = None,
        order_item_id: str | None = None,
        evidence: list[str] | None = None,
    ) -> dict[str, Any]:
        action = _text(action).upper()
        evidence_set = {_text(item).lower() for item in evidence or [] if _text(item)}
        if action not in SUPPORTED_ACTIONS:
            return self._result(
                CONFLICT,
                action=action,
                order_id=order_id,
                order_item_id=order_item_id,
                reason="不支持的售后动作",
            )
        if not get_settings().after_sales_policy_engine_enabled:
            return self._result(
                POLICY_UNAVAILABLE,
                action=action,
                order_id=order_id,
                order_item_id=order_item_id,
                reason="售后资格规则引擎当前已关闭",
            )
        if not user_id or (not order_id and not order_item_id):
            return self._result(
                CONFLICT,
                action=action,
                order_id=order_id,
                order_item_id=order_item_id,
                reason="缺少用户或订单引用",
            )

        item = await java_internal_client.get_order_item(order_item_id) if order_item_id else None
        if item is None and order_id:
            items = await java_internal_client.list_order_items(order_id)
            if len(items) == 1:
                item = items[0]
        if not item:
            return self._result(
                CONFLICT,
                action=action,
                order_id=order_id,
                order_item_id=order_item_id,
                reason="订单明细不存在或无法核验归属",
            )
        resolved_order_id = _text(item.get("order_id") or item.get("orderId") or order_id)
        resolved_item_id = _text(item.get("order_item_id") or item.get("orderItemId") or order_item_id)
        order = await java_internal_client.get_order(resolved_order_id)
        if not order or not _same(order.get("user_id") or order.get("userId"), user_id):
            return self._result(
                CONFLICT,
                action=action,
                order_id=resolved_order_id,
                order_item_id=resolved_item_id,
                reason="订单不属于当前用户或订单事实不完整",
            )

        facts = await self._facts(order, item)
        policies = await self._load_policies(action)
        selected, conflict = self._select_policy(policies, facts)
        if conflict:
            return await self._finish(
                self._result(
                    CONFLICT,
                    action=action,
                    order_id=resolved_order_id,
                    order_item_id=resolved_item_id,
                    reason="同一适用层级存在冲突的已发布售后规则",
                    facts=facts,
                ),
                user_id,
            )
        if selected is None:
            return await self._finish(
                self._result(
                    POLICY_UNAVAILABLE,
                    action=action,
                    order_id=resolved_order_id,
                    order_item_id=resolved_item_id,
                    reason="没有匹配的已发布售后规则",
                    facts=facts,
                ),
                user_id,
            )

        policy, specificity = selected
        result = self._evaluate_rule(
            policy,
            facts,
            evidence_set,
            action=action,
            order_id=resolved_order_id,
            order_item_id=resolved_item_id,
            specificity=specificity,
        )
        return await self._finish(result, user_id)

    async def _facts(self, order: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        product_id = _text(item.get("product_id") or item.get("productId"))
        product: dict[str, Any] = {}
        if product_id:
            try:
                product = await java_internal_client.get_product_detail(product_id) or {}
            except Exception as exc:
                logger.warning("after_sales_product_fact_unavailable", error=type(exc).__name__)
        return {
            "orderId": _text(order.get("order_id") or order.get("orderId")),
            "orderStatus": order.get("order_status", order.get("orderStatus")),
            "orderTime": order.get("order_time") or order.get("orderTime"),
            "orderItemId": _text(item.get("order_item_id") or item.get("orderItemId")),
            "itemStatus": item.get("order_item_status", item.get("orderItemStatus")),
            "productId": product_id,
            "skuKey": _text(item.get("property_value_id_hash") or item.get("propertyValueIdHash")),
            "categoryId": _text(
                product.get("category_id")
                or product.get("categoryId")
                or product.get("category_id_path")
            ),
        }

    async def _load_policies(self, action: str) -> list[dict[str, Any]]:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT policy_id, version, priority, scope_json, rule_json,
                       effective_start, effective_end
                FROM agent_after_sales_policy
                WHERE status='PUBLISHED'
                  AND effective_start <= NOW(3)
                  AND (effective_end IS NULL OR effective_end > NOW(3))
                ORDER BY priority DESC, effective_start DESC
                """
            )
            rows = await cur.fetchall()
        result: list[dict[str, Any]] = []
        for row in rows or []:
            scope = self._json_object(row.get("scope_json"))
            rule = self._json_object(row.get("rule_json"))
            actions = rule.get("action") or rule.get("actions") or action
            if isinstance(actions, str):
                actions = [actions]
            if any(_same(candidate, action) for candidate in actions or []):
                result.append({**row, "scope": scope, "rule": rule})
        return result

    @staticmethod
    def _select_policy(
        policies: list[dict[str, Any]], facts: dict[str, Any]
    ) -> tuple[tuple[dict[str, Any], int] | None, bool]:
        matches: list[tuple[int, int, str, dict[str, Any]]] = []
        for policy in policies:
            specificity = AfterSalesPolicyService._scope_specificity(policy.get("scope") or {}, facts)
            if specificity < 0:
                continue
            matches.append(
                (
                    specificity,
                    int(policy.get("priority") or 0),
                    _text(policy.get("effective_start")),
                    policy,
                )
            )
        if not matches:
            return None, False
        matches.sort(key=lambda value: (value[0], value[1], value[2]), reverse=True)
        best = matches[0]
        tied = [item[3] for item in matches if item[:2] == best[:2]]
        if len(tied) > 1:
            signatures = {json.dumps(item.get("rule"), sort_keys=True) for item in tied}
            if len(signatures) > 1:
                return None, True
        return (best[3], best[0]), False

    @staticmethod
    def _scope_specificity(scope: dict[str, Any], facts: dict[str, Any]) -> int:
        scope_type = _text(scope.get("scopeType") or scope.get("type")).upper()
        scope_id = _text(scope.get("scopeId") or scope.get("id"))
        if scope_type in {"", "GLOBAL"} and not any(
            scope.get(key) for key in ("skuKey", "productId", "categoryId", "category")
        ):
            return 0
        if scope_type in {"SKU", "ORDER_ITEM"} or scope.get("skuKey"):
            expected = scope_id or _text(scope.get("skuKey"))
            return 4 if expected and _same(expected, facts.get("skuKey")) else -1
        if scope_type == "PRODUCT" or scope.get("productId"):
            expected = scope_id or _text(scope.get("productId"))
            return 3 if expected and _same(expected, facts.get("productId")) else -1
        if scope_type in {"CATEGORY", "CATEGORY_ID"} or scope.get("categoryId") or scope.get("category"):
            expected = scope_id or _text(scope.get("categoryId") or scope.get("category"))
            return 2 if expected and _same(expected, facts.get("categoryId")) else -1
        return 0

    def _evaluate_rule(
        self,
        policy: dict[str, Any],
        facts: dict[str, Any],
        evidence: set[str],
        *,
        action: str,
        order_id: str,
        order_item_id: str,
        specificity: int,
    ) -> dict[str, Any]:
        rule = policy.get("rule") or {}
        base = self._result(
            ELIGIBLE,
            action=action,
            order_id=order_id,
            order_item_id=order_item_id,
            policy_id=_text(policy.get("policy_id")),
            policy_version=_text(policy.get("version")),
            facts=facts,
            specificity=specificity,
        )
        if not _in(rule.get("orderStatuses"), facts.get("orderStatus")):
            return {**base, "decision": INELIGIBLE, "reason": "当前订单状态不满足规则"}
        if not _in(rule.get("itemStatuses"), facts.get("itemStatus")):
            return {**base, "decision": INELIGIBLE, "reason": "当前订单项状态不满足规则"}
        order_time = _parse_time(facts.get("orderTime"))
        window_days = rule.get("windowDays")
        if window_days is not None and order_time is None:
            return {**base, "decision": NEEDS_EVIDENCE, "reason": "缺少可核验的下单时间"}
        if window_days is not None and order_time is not None:
            try:
                if (_now() - order_time).total_seconds() > float(window_days) * 86400:
                    return {**base, "decision": INELIGIBLE, "reason": "已超过售后时间窗口"}
            except (TypeError, ValueError):
                return {**base, "decision": CONFLICT, "reason": "规则时间窗口配置无效"}
        missing = [
            _text(item)
            for item in rule.get("requiredEvidence") or []
            if _text(item).lower() not in evidence
        ]
        if missing:
            return {
                **base,
                "decision": NEEDS_EVIDENCE,
                "missingEvidence": missing,
                "nextStep": "请补充所列凭证后再核验",
            }
        return base

    @staticmethod
    def _result(decision: str, **values: Any) -> dict[str, Any]:
        result = {
            "decision": decision,
            "policyVersion": values.pop("policy_version", None),
            "policyId": values.pop("policy_id", None),
            "missingEvidence": values.pop("missing_evidence", []),
            "nextStep": values.pop("next_step", None),
            "factReferences": values.pop("facts", {}),
        }
        result.update(values)
        return result

    async def _finish(self, result: dict[str, Any], user_id: str) -> dict[str, Any]:
        decision_id = _decision_id(
            user_id,
            result.get("orderId"),
            result.get("orderItemId"),
            result.get("action"),
            result.get("policyId"),
            result.get("policyVersion"),
            result.get("decision"),
        )
        result["decisionId"] = decision_id
        result["evaluatedAt"] = _now().isoformat()
        result.setdefault("risk", "LOW" if result["decision"] == ELIGIBLE else "MEDIUM")
        try:
            async with acquire() as cur:
                await cur.execute(
                    """
                    INSERT INTO agent_after_sales_eligibility
                        (decision_id,user_id,order_id,order_item_id,decision_json,
                         expires_at,created_at)
                    VALUES (%s,%s,%s,%s,%s,DATE_ADD(NOW(3), INTERVAL 5 MINUTE),NOW(3))
                    ON DUPLICATE KEY UPDATE decision_json=%s,
                        expires_at=DATE_ADD(NOW(3), INTERVAL 5 MINUTE)
                    """,
                    (
                        decision_id,
                        user_id,
                        result.get("orderId"),
                        result.get("orderItemId"),
                        json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
        except Exception as exc:
            logger.warning("after_sales_eligibility_persist_failed", error=type(exc).__name__)
        if result.get("decision") == NEEDS_EVIDENCE:
            missing = "、".join(str(item) for item in result.get("missingEvidence") or [])
            content = "售后申请仍需补充可核验凭证"
            if missing:
                content += f"：{missing}"
            try:
                await asyncio.wait_for(
                    java_internal_client.send_user_notification(
                        user_id,
                        title="售后凭证待补充",
                        content=content,
                        biz_type="after_sales_evidence",
                        biz_id=decision_id,
                    ),
                    timeout=2,
                )
            except Exception as exc:
                logger.warning(
                    "after_sales_evidence_notification_degraded",
                    error=type(exc).__name__,
                )
        return result

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
                return decoded if isinstance(decoded, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}


after_sales_policy_service = AfterSalesPolicyService()
