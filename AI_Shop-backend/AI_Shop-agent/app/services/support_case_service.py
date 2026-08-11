from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime

import structlog
from aiomysql import IntegrityError

from app.db.pool import acquire
from app.services.episode_service import (
    current_episode,
    episode_service,
    text_fingerprint,
)
from app.services.java_internal_client import java_internal_client
from app.services.pending_action_service import pending_action_service

logger = structlog.get_logger()

CASE_CATEGORIES = frozenset(
    {
        "DAMAGED",
        "WRONG_ITEM",
        "MISSING_ITEM",
        "LOGISTICS",
        "REFUND_DISPUTE",
        "PAYMENT_DISPUTE",
        "ADDRESS_CHANGE",
        "INVOICE",
        "COMPLAINT",
        "OTHER",
    }
)
CASE_STATUSES = frozenset({"OPEN", "IN_PROGRESS", "RESOLVED", "CANCELLED"})
# New cases use the sortable SC timestamp format. ``LEGACY-<case_id>`` is
# intentionally accepted for rows backfilled by the idempotent migration.
_CASE_NO_RE = re.compile(r"^(?:SC\d{8}[A-Z0-9]{6}|LEGACY-\d+)$")
_IMAGE_ASSET_ID_RE = re.compile(r"^img_[a-f0-9]{32}$")
CASE_CATEGORY_LABELS = {
    "DAMAGED": "商品破损",
    "WRONG_ITEM": "商品错发",
    "MISSING_ITEM": "商品少件",
    "LOGISTICS": "物流问题",
    "REFUND_DISPUTE": "退款争议",
    "PAYMENT_DISPUTE": "支付争议",
    "ADDRESS_CHANGE": "地址修改",
    "INVOICE": "发票问题",
    "COMPLAINT": "投诉",
    "OTHER": "其他",
}


def _json(value) -> str:
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"))


def _time(value) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else value


class SupportCaseService:
    """Independent after-sales case workflow.

    ``support_session`` remains the live human-chat channel. This table stores the
    business case itself, so one case can outlive a session and be audited/trained.
    """

    def _case_no(self) -> str:
        return f"SC{datetime.now():%Y%m%d}{uuid.uuid4().hex[:6].upper()}"

    @staticmethod
    def normalize_category(category: str | None) -> str:
        value = str(category or "OTHER").strip().upper()
        aliases = {
            "DAMAGED_OR_WRONG_ITEM": "DAMAGED",
            "WRONG": "WRONG_ITEM",
            "MISSING": "MISSING_ITEM",
            "DELIVERY": "LOGISTICS",
            "REFUND": "REFUND_DISPUTE",
            "PAYMENT": "PAYMENT_DISPUTE",
        }
        value = aliases.get(value, value)
        return value if value in CASE_CATEGORIES else "OTHER"

    @classmethod
    def category_for_intent(cls, intent: str | None, text: str | None = None) -> str:
        value = str(intent or "").upper()
        content = str(text or "")
        if value == "DAMAGED_OR_WRONG_ITEM":
            if any(
                term in content
                for term in ("少件", "漏发", "少发", "缺件", "少了", "缺少")
            ):
                return "MISSING_ITEM"
            if any(term in content for term in ("错发", "发错", "寄错", "不是我买")):
                return "WRONG_ITEM"
            return "DAMAGED"
        return cls.normalize_category(
            {
                "QUERY_LOGISTICS": "LOGISTICS",
                "QUERY_FULFILLMENT": "LOGISTICS",
                "REFUND": "REFUND_DISPUTE",
                "REFUND_STATUS": "REFUND_DISPUTE",
                "PAYMENT_ISSUE": "PAYMENT_DISPUTE",
                "ADDRESS_CHANGE": "ADDRESS_CHANGE",
                "INVOICE": "INVOICE",
                "COMPLAINT": "COMPLAINT",
            }.get(value, "OTHER")
        )

    async def _verify_order_owner(
        self, user_id: str, order_id: str | None, order_item_id: str | None
    ) -> tuple[dict | None, dict | None, str | None]:
        order = None
        item = None
        normalized_order_id = str(order_id or "").strip() or None
        normalized_item_id = str(order_item_id or "").strip() or None
        if normalized_item_id:
            item = await java_internal_client.get_order_item(normalized_item_id)
            if not item:
                raise ValueError("关联订单项不存在")
            item_order_id = str(
                item.get("order_id") or item.get("orderId") or ""
            ).strip()
            if not item_order_id:
                raise ValueError("关联订单项缺少订单信息")
            if normalized_order_id and normalized_order_id != item_order_id:
                raise ValueError("订单项不属于该订单")
            normalized_order_id = item_order_id
        if normalized_order_id:
            order = await java_internal_client.get_order(normalized_order_id)
            if not order:
                raise ValueError("关联订单不存在")
            if str(order.get("user_id") or order.get("userId") or "") != str(user_id):
                raise ValueError("只能关联本人订单")
        return order, item, normalized_order_id

    async def verify_image(
        self,
        user_id: str,
        image_asset_id: str | None,
    ) -> dict | None:
        asset_id = str(image_asset_id or "").strip()
        if not asset_id:
            return None
        if not _IMAGE_ASSET_ID_RE.fullmatch(asset_id):
            raise ValueError("售后图片资产标识无效，请重新上传")
        try:
            verified = await java_internal_client.verify_agent_image(user_id, asset_id)
        except Exception as exc:
            logger.warning("support_image_verify_failed", error=type(exc).__name__)
            raise ValueError("售后图片审核状态暂不可确认，请重新上传") from exc
        if not isinstance(verified, dict) or not verified.get("approved"):
            raise ValueError("售后图片尚未通过审核，不能提交工单")
        return {
            "imageAssetId": str(verified.get("asset_id") or asset_id),
            "contentSha256": str(verified.get("content_sha256") or ""),
            "mimeType": str(verified.get("mime_type") or ""),
            "width": int(verified.get("width") or 0),
            "height": int(verified.get("height") or 0),
            "moderationStatus": "APPROVED",
            "scene": "agent",
            "expiresAt": verified.get("expires_at"),
        }

    async def build_proposal(
        self,
        user_id: str,
        category: str,
        description: str,
        *,
        order_id: str | None = None,
        order_item_id: str | None = None,
        image_asset_id: str | None = None,
        image_understanding: str | None = None,
        image_understanding_status: str | None = None,
        run_id: str | None = None,
        source_message_id: int | None = None,
        forced_handoff: bool = False,
        priority: str = "NORMAL",
    ) -> dict:
        description = str(description or "").strip()
        if len(description) < 2:
            raise ValueError("请补充工单问题描述")
        if len(description) > 4000:
            description = description[:4000]
        normalized = self.normalize_category(category)
        order, item, order_id = await self._verify_order_owner(
            user_id, order_id, order_item_id
        )
        evidence = await self.verify_image(user_id, image_asset_id)
        if evidence:
            evidence["imageUnderstandingStatus"] = (
                str(image_understanding_status or "DISABLED").strip().upper()[:16]
            )
            if image_understanding:
                evidence["imageUnderstanding"] = str(image_understanding).strip()[:1000]
        description_hash = hashlib.sha256(description.encode("utf-8")).hexdigest()[:16]
        dedupe = (
            f"{user_id}:CASE:MESSAGE:{source_message_id}:{normalized}"
            if source_message_id is not None
            else f"{user_id}:CASE:{normalized}:{order_id or 'NO_ORDER'}:{description_hash}"
        )
        params = {
            "category": normalized,
            "categoryLabel": CASE_CATEGORY_LABELS[normalized],
            "description": description,
            "orderId": order_id,
            "orderItemId": order_item_id,
            "evidence": evidence,
            "sourceMessageId": source_message_id,
            "runId": run_id or (current_episode().run_id if current_episode() else None),
            "forcedHandoff": bool(forced_handoff),
            "priority": priority if priority in {"LOW", "NORMAL", "HIGH", "CRITICAL"} else "NORMAL",
            "caseDedupeKey": dedupe,
            "ownedOrderValidated": True,
        }
        if order:
            params["orderStatus"] = order.get("order_status") or order.get("orderStatus")
        if item:
            params["productName"] = item.get("product_name") or item.get("productName")
        return params

    async def create_from_pending(self, pending: dict, params: dict | None = None) -> dict:
        try:
            payload = params or json.loads(pending.get("paramsJson") or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("工单参数无效，请重新发起") from exc
        if not isinstance(payload, dict):
            raise ValueError("工单参数无效，请重新发起")
        user_id = str(pending.get("userId") or "")
        if not user_id:
            raise ValueError("工单用户身份缺失")
        return await self.create(
            user_id,
            payload.get("category"),
            payload.get("description"),
            order_id=payload.get("orderId"),
            order_item_id=payload.get("orderItemId"),
            evidence=payload.get("evidence"),
            source_message_id=payload.get("sourceMessageId") or pending.get("messageId"),
            run_id=pending.get("runId") or payload.get("runId"),
            action_token=pending.get("token"),
            priority=payload.get("priority") or "NORMAL",
            forced_handoff=bool(payload.get("forcedHandoff")),
            idempotency_key=pending.get("token"),
        )

    async def create(
        self,
        user_id: str,
        category: str,
        description: str,
        *,
        order_id: str | None = None,
        order_item_id: str | None = None,
        evidence: dict | None = None,
        source_message_id: int | None = None,
        run_id: str | None = None,
        action_token: str | None = None,
        idempotency_key: str | None = None,
        priority: str = "NORMAL",
        forced_handoff: bool = False,
        support_session_id: str | None = None,
    ) -> dict:
        category = self.normalize_category(category)
        description = str(description or "").strip()[:4000]
        if len(description) < 2:
            raise ValueError("请补充工单问题描述")
        if idempotency_key:
            existing = await self.get_by_idempotency(user_id, idempotency_key)
            if existing:
                return existing
        evidence = await self._retain_case_evidence(user_id, evidence)
        _order, _item, order_id = await self._verify_order_owner(
            user_id, order_id, order_item_id
        )
        case_no = self._case_no()
        try:
            async with acquire() as cur:
                await cur.execute(
                    """
                    INSERT INTO support_case
                        (case_no,user_id,order_id,order_item_id,category,status,description,
                         evidence_json,source_message_id,run_id,action_token,idempotency_key,
                         priority,forced_handoff,support_session_id,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,'OPEN',%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(3),NOW(3))
                    """,
                    (
                        case_no,
                        user_id,
                        order_id,
                        order_item_id,
                        category,
                        description,
                        _json(evidence),
                        source_message_id,
                        run_id,
                        action_token,
                        idempotency_key,
                        priority,
                        1 if forced_handoff else 0,
                        support_session_id,
                    ),
                )
                case_id = int(cur.lastrowid)
        except IntegrityError as exc:
            if exc.args and exc.args[0] == 1062 and idempotency_key:
                existing = await self.get_by_idempotency(user_id, idempotency_key)
                if existing:
                    return existing
            raise
        result = await self.get(case_id)
        if not result:
            raise RuntimeError("工单写入后无法读取")
        episode_service.update_run(
            run_id=run_id,
            scenario="ORDER_AFTERSALES",
            reward_signals={
                "caseCreated": True,
                "caseCategory": category,
                "caseStatus": "OPEN",
                "forcedHandoff": bool(forced_handoff),
            },
        )
        episode_service.record_step(
            "SUPPORT_CASE_CREATED",
            run_id=run_id,
            node_name="support_case",
            output_data={
                "caseId": result.get("caseId"),
                "caseNo": result.get("caseNo"),
                "category": category,
                "forcedHandoff": bool(forced_handoff),
            },
        )
        await self._publish("support_case.created", result)
        return result

    async def _retain_case_evidence(
        self, user_id: str, evidence: dict | None
    ) -> dict | None:
        if not evidence:
            return None
        asset_id = str(evidence.get("imageAssetId") or "").strip()
        if not asset_id:
            raise ValueError("工单图片证据缺少图片资产标识")
        refreshed = await self.verify_image(user_id, asset_id)
        if refreshed is None:
            raise ValueError("工单图片证据不可用")
        for key in ("imageUnderstandingStatus", "imageUnderstanding"):
            if evidence.get(key):
                refreshed[key] = evidence[key]
        await java_internal_client.retain_agent_image_as_support_evidence(
            user_id, asset_id
        )
        refreshed["retentionClass"] = "SUPPORT_EVIDENCE"
        refreshed["expiresAt"] = None
        return refreshed

    async def get_by_idempotency(self, user_id: str, key: str) -> dict | None:
        async with acquire() as cur:
            await cur.execute(
                "SELECT * FROM support_case WHERE user_id=%s AND idempotency_key=%s",
                (user_id, key),
            )
            row = await cur.fetchone()
        return self.public(row) if row else None

    async def get(self, case_id_or_no: str | int) -> dict | None:
        value = str(case_id_or_no).strip()
        if not value:
            return None
        if value.isdigit():
            predicate = "case_id=%s"
        elif _CASE_NO_RE.fullmatch(value):
            predicate = "case_no=%s"
        else:
            return None
        async with acquire() as cur:
            await cur.execute(
                f"SELECT * FROM support_case WHERE {predicate} LIMIT 1",
                (value,),
            )
            row = await cur.fetchone()
        return self.public(row) if row else None

    async def list_for_user(self, user_id: str, case_id: str | None = None, limit: int = 20) -> list[dict]:
        limit = max(1, min(int(limit or 20), 50))
        async with acquire() as cur:
            if case_id:
                value = str(case_id).strip()
                if value.isdigit():
                    predicate = "case_id=%s"
                elif _CASE_NO_RE.fullmatch(value):
                    predicate = "case_no=%s"
                else:
                    return []
                await cur.execute(
                    f"SELECT * FROM support_case WHERE user_id=%s AND {predicate}",
                    (user_id, value),
                )
            else:
                await cur.execute(
                    "SELECT * FROM support_case WHERE user_id=%s ORDER BY updated_at DESC LIMIT %s",
                    (user_id, limit),
                )
            rows = await cur.fetchall()
        return [self.public(row) for row in rows]

    async def list_admin(
        self, page_no: int = 1, page_size: int = 30, status: str | None = None, user_id: str | None = None
    ) -> dict:
        page_no = max(1, int(page_no or 1))
        page_size = max(1, min(int(page_size or 30), 100))
        where = ["1=1"]
        params: list = []
        if status:
            if status.upper() not in CASE_STATUSES:
                raise ValueError("工单状态无效")
            where.append("status=%s")
            params.append(status.upper())
        if user_id:
            where.append("user_id=%s")
            params.append(user_id)
        predicate = " AND ".join(where)
        async with acquire() as cur:
            await cur.execute(f"SELECT COUNT(*) AS cnt FROM support_case WHERE {predicate}", params)
            total = int((await cur.fetchone())['cnt'])
            await cur.execute(
                f"SELECT * FROM support_case WHERE {predicate} ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                [*params, page_size, (page_no - 1) * page_size],
            )
            rows = await cur.fetchall()
        return {
            "totalCount": total,
            "pageNo": page_no,
            "pageSize": page_size,
            "pageTotal": (total + page_size - 1) // page_size if total else 0,
            "list": [self.public(row) for row in rows],
        }

    async def claim(self, case_id: str, admin_id: str) -> dict:
        async with acquire() as cur:
            await cur.execute(
                "UPDATE support_case SET status='IN_PROGRESS', assigned_admin=%s, updated_at=NOW(3) "
                "WHERE (case_id=%s OR case_no=%s) AND status='OPEN'",
                (admin_id, case_id, case_id),
            )
            if cur.rowcount != 1:
                raise ValueError("工单不存在、已认领或已结束")
        result = await self.get(case_id)
        if result:
            await self._publish("support_case.updated", result)
        return result or {}

    async def in_progress(self, case_id: str, admin_id: str) -> dict:
        """Move an already claimed case into active processing idempotently."""
        admin_id = str(admin_id or "").strip()
        if not admin_id:
            raise ValueError("adminId 不能为空")
        async with acquire() as cur:
            await cur.execute(
                "UPDATE support_case SET status='IN_PROGRESS', updated_at=NOW(3) "
                "WHERE (case_id=%s OR case_no=%s) AND status IN ('OPEN','IN_PROGRESS') "
                "AND (assigned_admin IS NULL OR assigned_admin=%s)",
                (case_id, case_id, admin_id),
            )
            if cur.rowcount != 1:
                raise ValueError("工单不存在、已结束或不属于当前客服")
            if cur.rowcount == 1:
                await cur.execute(
                    "UPDATE support_case SET assigned_admin=COALESCE(assigned_admin,%s) "
                    "WHERE (case_id=%s OR case_no=%s)",
                    (admin_id, case_id, case_id),
                )
        result = await self.get(case_id)
        if result:
            await self._publish("support_case.updated", result)
        return result or {}

    async def link_session(self, case_id: str | int, session_id: str) -> dict:
        if not str(session_id or "").strip():
            raise ValueError("supportSessionId 不能为空")
        value = str(case_id).strip()
        predicate = "case_id=%s" if value.isdigit() else "case_no=%s"
        async with acquire() as cur:
            await cur.execute(
                f"UPDATE support_case SET support_session_id=%s, updated_at=NOW(3) "
                f"WHERE {predicate}",
                (session_id, value),
            )
            if cur.rowcount != 1:
                raise ValueError("工单不存在")
        return await self.get(value) or {}

    async def resolve(
        self,
        case_id: str,
        admin_id: str,
        resolution_code: str,
        root_cause: str,
        resolution_summary: str,
        support_session_id: str | None = None,
    ) -> dict:
        for name, value in {
            "resolutionCode": resolution_code,
            "rootCause": root_cause,
            "resolutionSummary": resolution_summary,
        }.items():
            if not str(value or "").strip():
                raise ValueError(f"{name} 不能为空")
        existing = await self.get(case_id)
        linked_session_id = support_session_id or (existing or {}).get(
            "supportSessionId"
        )
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE support_case
                SET status='RESOLVED', assigned_admin=COALESCE(assigned_admin,%s),
                    resolution_code=%s, root_cause=%s, resolution_summary=%s,
                    support_session_id=COALESCE(%s,support_session_id),
                    resolved_at=NOW(3), updated_at=NOW(3)
                WHERE (case_id=%s OR case_no=%s)
                  AND status IN ('OPEN','IN_PROGRESS')
                  AND (assigned_admin IS NULL OR assigned_admin=%s)
                """,
                (
                    admin_id,
                    resolution_code.strip(),
                    root_cause.strip(),
                    resolution_summary.strip(),
                    linked_session_id,
                    case_id,
                    case_id,
                    admin_id,
                ),
            )
            if cur.rowcount != 1:
                raise ValueError("工单不存在、已解决或不属于当前客服")
        result = await self.get(case_id)
        run_id = (result or {}).get("runId")
        episode_service.update_run(
            run_id=run_id,
            reward_signals={
                "humanResolutionCode": resolution_code.strip(),
                "humanRootCause": root_cause.strip(),
                "humanResolved": True,
                "humanResolutionSummaryFingerprint": text_fingerprint(
                    resolution_summary.strip()
                ),
                "supportSessionId": linked_session_id,
            },
        )
        episode_service.record_step(
            "SUPPORT_CASE_RESOLVED",
            run_id=run_id,
            node_name="support_case",
            output_data={
                "resolutionCode": resolution_code.strip(),
                "supportSessionId": linked_session_id,
            },
        )
        if linked_session_id:
            try:
                from app.services.support_service import support_service

                await support_service.resolve(
                    linked_session_id,
                    admin_id,
                    resolution_summary.strip(),
                )
            except ValueError as exc:
                logger.info(
                    "support_session_already_terminal",
                    session_id=linked_session_id,
                    reason=str(exc),
                )
        if result:
            await self._publish("support_case.resolved", result)
        return result or {}

    async def _publish(self, event: str, case: dict) -> None:
        try:
            from app.services.support_service import support_service

            await support_service.publish_admin({"event": event, "case": case})
        except Exception as exc:
            logger.warning(
                "support_case_publish_failed",
                event=event,
                error=type(exc).__name__,
            )

    async def propose(
        self,
        user_id: str,
        category: str,
        description: str,
        **kwargs,
    ) -> str:
        params = await self.build_proposal(user_id, category, description, **kwargs)
        forced = bool(params.get("forcedHandoff"))
        if forced:
            case = await self.create(
                user_id,
                params["category"],
                params["description"],
                order_id=params.get("orderId"),
                order_item_id=params.get("orderItemId"),
                evidence=params.get("evidence"),
                source_message_id=params.get("sourceMessageId"),
                run_id=params.get("runId"),
                priority=params.get("priority") or "CRITICAL",
                forced_handoff=True,
                idempotency_key=params.get("caseDedupeKey"),
            )
            return f"已为您创建售后工单 {case['caseNo']}，该问题将立即转人工处理。"
        pending = await pending_action_service.create_pending(
            "CREATE_SUPPORT_CASE",
            user_id,
            params,
            f"创建售后工单：{params['category']}，{params['description'][:80]}",
            run_id=params.get("runId"),
        )
        return (
            f"已生成创建售后工单确认卡片。请确认后提交，回复末尾附带【{pending['token']}】"
        )

    async def public_query(self, user_id: str, case_id: str | None = None) -> str:
        rows = await self.list_for_user(user_id, case_id)
        if case_id and not rows:
            return "【工单查询失败】工单不存在或无权查看"
        return json.dumps(rows, ensure_ascii=False)

    @staticmethod
    def public(row: dict | None) -> dict:
        if not row:
            return {}
        evidence = row.get("evidence_json")
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except json.JSONDecodeError:
                evidence = {}
        return {
            "caseId": row.get("case_id"),
            "caseNo": row.get("case_no"),
            "userId": row.get("user_id"),
            "orderId": row.get("order_id"),
            "orderItemId": row.get("order_item_id"),
            "category": row.get("category"),
            "categoryLabel": CASE_CATEGORY_LABELS.get(
                str(row.get("category") or "OTHER"), "其他"
            ),
            "status": row.get("status"),
            "description": row.get("description"),
            "evidence": evidence or {},
            "sourceMessageId": row.get("source_message_id"),
            "runId": row.get("run_id"),
            "actionToken": row.get("action_token"),
            "priority": row.get("priority"),
            "forcedHandoff": bool(row.get("forced_handoff")),
            "supportSessionId": row.get("support_session_id"),
            "assignedAdmin": row.get("assigned_admin"),
            "resolutionCode": row.get("resolution_code"),
            "rootCause": row.get("root_cause"),
            "resolutionSummary": row.get("resolution_summary"),
            "createdAt": _time(row.get("created_at")),
            "updatedAt": _time(row.get("updated_at")),
            "resolvedAt": _time(row.get("resolved_at")),
        }


support_case_service = SupportCaseService()
