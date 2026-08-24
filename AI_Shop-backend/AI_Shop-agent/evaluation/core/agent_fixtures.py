"""Explicit local-only fixtures for authoritative Agent write evaluation.

Fixture rows are provisioned directly because the application intentionally has
no production endpoint for manufacturing an unpaid order. The operation under
test still runs through the normal Agent confirmation and Java order APIs.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import aiomysql

from app.config.settings import get_settings

_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})
_CANCELABLE_ORDER = "CANCELABLE_ORDER_V1"
_CUSTOMER_SERVICE_ORDER = "CUSTOMER_SERVICE_ORDER_V1"
_LOCAL_SCOPE = "LOCAL_EVALUATION_ONLY"
_JAVA_TOKEN_USER_CLASS = "com.aishop.entity.dto.TokenUserInfoDTO"
_INITIAL_STOCK = 7
_ORDER_QUANTITY = 1
_AGENT_MUTABLE_TABLES = (
    "agent_message_feedback",
    "agent_task",
    "agent_pending_action",
    "agent_order_selection",
    "agent_visual_selection",
    "agent_session_memory",
    "agent_after_sales_eligibility",
    "agent_final_offer_snapshot",
    "agent_recommendation_event",
    "agent_shopping_mission",
    "agent_shopping_profile",
    "commerce_outcome_ledger",
    "support_case",
)


def write_fixtures_enabled() -> bool:
    return os.getenv("AI_EVAL_ENABLE_WRITE_FIXTURES", "").strip().casefold() in _ENABLED_VALUES


def _fixture_guard(declaration: dict[str, Any]) -> None:
    settings = get_settings()
    if settings.app_env.strip().casefold() == "production":
        raise RuntimeError("Agent write fixtures are forbidden in production")
    if not write_fixtures_enabled():
        raise RuntimeError(
            "Agent write fixture requested without AI_EVAL_ENABLE_WRITE_FIXTURES=true"
        )
    if declaration.get("scope") != _LOCAL_SCOPE:
        raise RuntimeError("Agent write fixture must declare LOCAL_EVALUATION_ONLY scope")


async def _connect_order_db(*, autocommit: bool = True) -> aiomysql.Connection:
    settings = get_settings()
    return await aiomysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        db="aishop_order",
        charset="utf8mb4",
        autocommit=autocommit,
    )


def _stable_fixture_ids(user_id: str, nonce: str) -> dict[str, str]:
    digest = hashlib.sha256(f"{user_id}\0{nonce}".encode()).hexdigest().upper()
    # Java StringTools.createOrderId() emits a 17-digit millisecond timestamp
    # followed by 15 alphanumeric characters. Derive a valid, stable timestamp
    # inside a bounded fixture epoch so each trial remains reproducible while
    # exercising the production order-reference parser unchanged.
    fixture_epoch = datetime(2020, 1, 1) + timedelta(
        milliseconds=int(digest[:16], 16) % (10 * 365 * 24 * 60 * 60 * 1000)
    )
    timestamp = fixture_epoch.strftime("%Y%m%d%H%M%S%f")[:17]
    order_id = timestamp + digest[16:31]
    product_id = "EV" + digest[24:37]
    sku_hash = digest[32:64]
    canonical = f"{order_id}\0{product_id}\0{sku_hash}".encode()
    return {
        "orderId": order_id,
        "orderItemId": f"{order_id}_1",
        "productId": product_id,
        "skuHash": sku_hash,
        "restoreBusinessKey": "order-close:" + hashlib.sha256(canonical).hexdigest(),
        "compensationKey": f"remote:stock:orderRestore:{order_id}",
    }


def _stable_customer_service_order_ids(user_id: str, nonce: str) -> dict[str, str]:
    """Generate isolated identifiers for a read-only客服 order snapshot."""

    digest = hashlib.sha256(f"customer-service\0{user_id}\0{nonce}".encode()).hexdigest().upper()
    fixture_epoch = datetime(2020, 1, 1) + timedelta(
        milliseconds=int(digest[:16], 16) % (10 * 365 * 24 * 60 * 60 * 1000)
    )
    timestamp = fixture_epoch.strftime("%Y%m%d%H%M%S%f")[:17]
    order_id = timestamp + digest[16:31]
    product_id = "EV" + digest[24:37]
    sku_hash = digest[32:64]
    return {
        "orderId": order_id,
        "orderItemId": f"{order_id}_1",
        "productId": product_id,
        "skuHash": sku_hash,
    }


def build_java_web_session_payload(
    declaration: dict[str, Any], *, user_id: str, token: str
) -> dict[str, Any]:
    """Build a RedisSerializer.json-compatible principal for local write evals."""

    _fixture_guard(declaration)
    if declaration.get("kind") not in {_CANCELABLE_ORDER, _CUSTOMER_SERVICE_ORDER}:
        raise RuntimeError("Java-compatible session requires a supported local order fixture")
    if not user_id.strip() or not token.strip():
        raise RuntimeError("Java-compatible session requires non-empty userId and token")
    return {
        "@class": _JAVA_TOKEN_USER_CLASS,
        "userId": user_id,
        "email": None,
        "nickName": "evaluation",
        "avatar": None,
        "token": token,
    }


@dataclass
class ProvisionedAgentFixture:
    declared: dict[str, Any]
    user_id: str
    order_id: str | None = None
    order_item_id: str | None = None
    product_id: str | None = None
    sku_hash: str | None = None
    restore_business_key: str | None = None
    compensation_key: str | None = None
    capture_fixture: dict[str, Any] = field(default_factory=dict)
    template_values: dict[str, str] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    cleanup_mode: str = "ORDER_AND_STOCK"

    @property
    def active(self) -> bool:
        return bool(self.order_id)

    async def cleanup(self) -> None:
        if not self.order_id:
            return

        agent_cleanup: dict[str, int] = {}
        agent_residue: dict[str, int] = {}
        agent_error: Exception | None = None
        try:
            # Episodes are copied into run evidence before cleanup. Only mutable
            # user/session rows are removed here; trace rows remain auditable.
            from app.db.pool import transaction

            async with transaction() as cursor:
                await cursor.execute(
                    "SELECT run_id FROM agent_run WHERE user_id=%s",
                    (self.user_id,),
                )
                run_ids = [
                    str(row.get("run_id") or "")
                    for row in await cursor.fetchall()
                    if row.get("run_id")
                ]
                await cursor.execute(
                    """
                    DELETE b FROM ai_badcase_candidate b
                    LEFT JOIN agent_run r ON r.run_id=b.run_id
                    LEFT JOIN agent_message m ON m.message_id=b.message_id
                    WHERE r.user_id=%s OR m.user_id=%s
                    """,
                    (self.user_id, self.user_id),
                )
                agent_cleanup["ai_badcase_candidate"] = int(cursor.rowcount or 0)
                await cursor.execute(
                    """
                    DELETE h FROM agent_handoff h
                    LEFT JOIN agent_run p ON p.run_id=h.parent_run_id
                    LEFT JOIN agent_run c ON c.run_id=h.child_run_id
                    WHERE p.user_id=%s OR c.user_id=%s
                    """,
                    (self.user_id, self.user_id),
                )
                agent_cleanup["agent_handoff"] = int(cursor.rowcount or 0)
                await cursor.execute(
                    """
                    DELETE s FROM agent_step s
                    INNER JOIN agent_run r ON r.run_id=s.run_id
                    WHERE r.user_id=%s
                    """,
                    (self.user_id,),
                )
                agent_cleanup["agent_step"] = int(cursor.rowcount or 0)
                await cursor.execute(
                    """
                    DELETE e FROM agent_recommendation_explanation e
                    INNER JOIN agent_ranking_policy_decision d
                        ON d.decision_id=e.decision_id
                    WHERE d.user_id=%s
                    """,
                    (self.user_id,),
                )
                agent_cleanup["agent_recommendation_explanation"] = int(
                    cursor.rowcount or 0
                )
                for table in _AGENT_MUTABLE_TABLES:
                    await cursor.execute(
                        f"DELETE FROM {table} WHERE user_id=%s", (self.user_id,)
                    )
                    agent_cleanup[table] = int(cursor.rowcount or 0)
                await cursor.execute(
                    "DELETE FROM agent_ranking_policy_decision WHERE user_id=%s",
                    (self.user_id,),
                )
                agent_cleanup["agent_ranking_policy_decision"] = int(
                    cursor.rowcount or 0
                )
                await cursor.execute(
                    "DELETE FROM agent_run WHERE user_id=%s", (self.user_id,)
                )
                agent_cleanup["agent_run"] = int(cursor.rowcount or 0)
                await cursor.execute(
                    "DELETE FROM agent_message WHERE user_id=%s", (self.user_id,)
                )
                agent_cleanup["agent_message"] = int(cursor.rowcount or 0)
                for table in _AGENT_MUTABLE_TABLES:
                    await cursor.execute(
                        f"SELECT COUNT(*) AS residual FROM {table} WHERE user_id=%s",
                        (self.user_id,),
                    )
                    row = await cursor.fetchone()
                    agent_residue[table] = int((row or {}).get("residual") or 0)
                for table in (
                    "agent_ranking_policy_decision",
                    "agent_run",
                    "agent_message",
                ):
                    await cursor.execute(
                        f"SELECT COUNT(*) AS residual FROM {table} WHERE user_id=%s",
                        (self.user_id,),
                    )
                    row = await cursor.fetchone()
                    agent_residue[table] = int((row or {}).get("residual") or 0)
                if run_ids:
                    placeholders = ",".join(["%s"] * len(run_ids))
                    await cursor.execute(
                        f"SELECT COUNT(*) AS residual FROM agent_step "
                        f"WHERE run_id IN ({placeholders})",
                        tuple(run_ids),
                    )
                    row = await cursor.fetchone()
                    agent_residue["agent_step"] = int(
                        (row or {}).get("residual") or 0
                    )
                if any(agent_residue.values()):
                    raise RuntimeError(f"Agent fixture cleanup residue: {agent_residue}")
        except Exception as exc:
            agent_error = exc
            self.evidence["cleanupAgentDbError"] = type(exc).__name__

        java_cleanup: dict[str, int] = {}
        java_residue: dict[str, int] = {"orderRows": 1}
        java_error: Exception | None = None
        try:
            if not self.order_item_id:
                raise RuntimeError("evaluation fixture cleanup order identifiers are incomplete")
            connection = await _connect_order_db(autocommit=False)
            try:
                async with connection.cursor() as cursor:
                    if self.cleanup_mode == "ORDER_ONLY":
                        await cursor.execute(
                            "DELETE FROM refund_request WHERE order_id=%s AND user_id=%s",
                            (self.order_id, self.user_id),
                        )
                        java_cleanup["refundRequests"] = int(cursor.rowcount or 0)
                        await cursor.execute(
                            "DELETE FROM order_logistics_info_record WHERE order_id=%s",
                            (self.order_id,),
                        )
                        java_cleanup["logisticsRecords"] = int(cursor.rowcount or 0)
                        await cursor.execute(
                            "DELETE FROM order_logistics_info WHERE order_id=%s AND user_id=%s",
                            (self.order_id, self.user_id),
                        )
                        java_cleanup["logistics"] = int(cursor.rowcount or 0)
                        await cursor.execute(
                            "DELETE FROM order_item WHERE order_item_id=%s AND order_id=%s",
                            (self.order_item_id, self.order_id),
                        )
                        java_cleanup["orderItems"] = int(cursor.rowcount or 0)
                        await cursor.execute(
                            "DELETE FROM order_info WHERE order_id=%s AND user_id=%s",
                            (self.order_id, self.user_id),
                        )
                        java_cleanup["orders"] = int(cursor.rowcount or 0)
                        residue_queries = (
                            (
                                "orderRows",
                                "SELECT COUNT(*) FROM order_info WHERE order_id=%s",
                                (self.order_id,),
                            ),
                            (
                                "orderItemRows",
                                "SELECT COUNT(*) FROM order_item WHERE order_item_id=%s",
                                (self.order_item_id,),
                            ),
                            (
                                "logisticsRows",
                                "SELECT COUNT(*) FROM order_logistics_info WHERE order_id=%s",
                                (self.order_id,),
                            ),
                            (
                                "logisticsRecordRows",
                                "SELECT COUNT(*) FROM order_logistics_info_record WHERE order_id=%s",
                                (self.order_id,),
                            ),
                            (
                                "refundRows",
                                "SELECT COUNT(*) FROM refund_request WHERE order_id=%s",
                                (self.order_id,),
                            ),
                        )
                        for name, sql, params in residue_queries:
                            await cursor.execute(sql, params)
                            row = await cursor.fetchone()
                            java_residue[name] = int(row[0]) if row else 1
                        await connection.commit()
                        # The order-only fixture has no inventory or durable
                        # command ledger.  Skip the write-fixture cleanup below.
                        self.evidence["cleanup"] = {
                            "completed": (
                                not any(java_residue.values())
                                and agent_error is None
                                and java_error is None
                            ),
                            "agentRowsDeleted": agent_cleanup,
                            "residualAgentRows": agent_residue,
                            "javaRowsDeleted": java_cleanup,
                            "residualJavaRows": java_residue,
                        }
                        if any(java_residue.values()):
                            raise RuntimeError(
                                f"evaluation order fixture cleanup residue: {java_residue}"
                            )
                        if agent_error is not None:
                            raise RuntimeError(
                                "Agent fixture cleanup failed after Java cleanup"
                            ) from agent_error
                        return
                    identifiers = (
                        self.order_item_id,
                        self.product_id,
                        self.sku_hash,
                        self.restore_business_key,
                        self.compensation_key,
                    )
                    if not all(identifiers):
                        raise RuntimeError("evaluation fixture cleanup identifiers are incomplete")
                    await cursor.execute(
                        "DELETE FROM order_item WHERE order_item_id=%s AND order_id=%s",
                        (self.order_item_id, self.order_id),
                    )
                    java_cleanup["orderItems"] = int(cursor.rowcount or 0)
                    await cursor.execute(
                        "DELETE FROM order_request_idempotency WHERE user_id=%s",
                        (self.user_id,),
                    )
                    java_cleanup["orderCommandLedger"] = int(cursor.rowcount or 0)
                    await cursor.execute(
                        "DELETE FROM mq_compensation_log WHERE idempotency_key=%s",
                        (self.compensation_key,),
                    )
                    java_cleanup["orderCompensationLogs"] = int(cursor.rowcount or 0)
                    await cursor.execute(
                        "DELETE FROM order_info WHERE order_id=%s AND user_id=%s",
                        (self.order_id, self.user_id),
                    )
                    java_cleanup["orders"] = int(cursor.rowcount or 0)
                    await cursor.execute(
                        """
                        DELETE FROM aishop_stock.stock_change_record
                        WHERE business_key=%s AND product_id=%s
                          AND property_value_id_hash=%s
                        """,
                        (self.restore_business_key, self.product_id, self.sku_hash),
                    )
                    java_cleanup["stockChangeRecords"] = int(cursor.rowcount or 0)
                    await cursor.execute(
                        """
                        DELETE FROM aishop_stock.sku_stock
                        WHERE product_id=%s AND property_value_id_hash=%s
                        """,
                        (self.product_id, self.sku_hash),
                    )
                    java_cleanup["inventoryRows"] = int(cursor.rowcount or 0)

                    residue_queries = (
                        (
                            "orderRows",
                            "SELECT COUNT(*) FROM order_info WHERE order_id=%s",
                            (self.order_id,),
                        ),
                        (
                            "orderItemRows",
                            "SELECT COUNT(*) FROM order_item WHERE order_item_id=%s",
                            (self.order_item_id,),
                        ),
                        (
                            "ledgerRows",
                            "SELECT COUNT(*) FROM order_request_idempotency WHERE user_id=%s",
                            (self.user_id,),
                        ),
                        (
                            "compensationRows",
                            "SELECT COUNT(*) FROM mq_compensation_log WHERE idempotency_key=%s",
                            (self.compensation_key,),
                        ),
                        (
                            "stockChangeRows",
                            """SELECT COUNT(*) FROM aishop_stock.stock_change_record
                               WHERE business_key=%s""",
                            (self.restore_business_key,),
                        ),
                        (
                            "inventoryRows",
                            """SELECT COUNT(*) FROM aishop_stock.sku_stock
                               WHERE product_id=%s AND property_value_id_hash=%s""",
                            (self.product_id, self.sku_hash),
                        ),
                    )
                    for name, sql, params in residue_queries:
                        await cursor.execute(sql, params)
                        row = await cursor.fetchone()
                        java_residue[name] = int(row[0]) if row else 1
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
            finally:
                connection.close()
        except Exception as exc:
            java_error = exc
            self.evidence["cleanupJavaDbError"] = type(exc).__name__

        self.evidence["cleanup"] = {
            "completed": (
                not any(agent_residue.values())
                and not any(java_residue.values())
                and agent_error is None
                and java_error is None
            ),
            "agentRowsDeleted": agent_cleanup,
            "residualAgentRows": agent_residue,
            "javaRowsDeleted": java_cleanup,
            "residualJavaRows": java_residue,
        }
        if java_error is not None:
            raise RuntimeError("evaluation Java fixture cleanup failed") from java_error
        if any(java_residue.values()):
            raise RuntimeError(f"evaluation fixture cleanup left residue: {java_residue}")
        if agent_error is not None:
            raise RuntimeError("Agent fixture cleanup failed after Java cleanup") from agent_error


async def provision_agent_fixture(
    declaration: dict[str, Any] | None,
    *,
    user_id: str,
    isolation_nonce: str | None = None,
) -> ProvisionedAgentFixture:
    declared = dict(declaration or {})
    kind = str(declared.get("kind") or "").strip()
    if not kind:
        return ProvisionedAgentFixture(declared=declared, user_id=user_id)
    if kind == _CUSTOMER_SERVICE_ORDER:
        return await _provision_customer_service_order_fixture(
            declared,
            user_id=user_id,
            isolation_nonce=isolation_nonce,
        )
    if kind != _CANCELABLE_ORDER:
        raise RuntimeError(f"unsupported Agent state fixture kind: {kind}")
    _fixture_guard(declared)

    nonce = isolation_nonce or secrets.token_hex(16)
    ids = _stable_fixture_ids(user_id, nonce)
    order_id = ids["orderId"]
    connection = await _connect_order_db(autocommit=False)
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO aishop_stock.sku_stock
                    (product_id, property_value_id_hash, stock)
                VALUES (%s, %s, %s)
                """,
                (ids["productId"], ids["skuHash"], _INITIAL_STOCK),
            )
            if int(cursor.rowcount or 0) != 1:
                raise RuntimeError("failed to provision exactly one evaluation inventory row")
            await cursor.execute(
                """
                INSERT INTO order_info
                    (order_id, amount, goods_amount, discount_amount, coupon_discount,
                     user_id, order_time, order_status, comment_status, subject)
                VALUES (%s, 1.00, 1.00, 0.00, 0.00, %s, NOW(3), 0, 0, %s)
                """,
                (order_id, user_id, "AI evaluation cancelable order"),
            )
            if int(cursor.rowcount or 0) != 1:
                raise RuntimeError("failed to provision exactly one evaluation order")
            await cursor.execute(
                """
                INSERT INTO order_item
                    (order_item_id, order_id, product_id, product_name,
                     property_value_id_hash, property_info, item_amount,
                     buy_count, order_item_status, remark)
                VALUES (%s, %s, %s, %s, %s, %s, 1.00, %s, 1, %s)
                """,
                (
                    ids["orderItemId"],
                    order_id,
                    ids["productId"],
                    "AI evaluation inventory fixture",
                    ids["skuHash"],
                    "evaluation",
                    _ORDER_QUANTITY,
                    _LOCAL_SCOPE,
                ),
            )
            if int(cursor.rowcount or 0) != 1:
                raise RuntimeError("failed to provision exactly one evaluation order item")
            await cursor.execute(
                "SELECT order_status FROM order_info WHERE order_id=%s AND user_id=%s",
                (order_id, user_id),
            )
            row = await cursor.fetchone()
            if not row or int(row[0]) != 0:
                raise RuntimeError("evaluation order fixture is not WAIT_PAYMENT")
            await cursor.execute(
                """SELECT stock FROM aishop_stock.sku_stock
                   WHERE product_id=%s AND property_value_id_hash=%s""",
                (ids["productId"], ids["skuHash"]),
            )
            stock_row = await cursor.fetchone()
            if not stock_row or int(stock_row[0]) != _INITIAL_STOCK:
                raise RuntimeError("evaluation inventory fixture has an unexpected stock value")
        await connection.commit()
    except BaseException:
        await connection.rollback()
        raise
    finally:
        connection.close()

    capture_fixture = {
        **declared,
        "orderIds": [order_id],
        "orderDatabaseAudit": {
            "enabled": True,
            "commandTypes": ["AGENT_CANCEL_ORDER"],
            "inventory": {
                "productId": ids["productId"],
                "propertyValueIdHash": ids["skuHash"],
                "restoreBusinessKey": ids["restoreBusinessKey"],
                "compensationKey": ids["compensationKey"],
                "expectedRestoreDelta": _ORDER_QUANTITY,
            },
        },
    }
    evidence = {
        "kind": kind,
        "scope": _LOCAL_SCOPE,
        "orderId": order_id,
        "orderItemId": ids["orderItemId"],
        "productId": ids["productId"],
        "propertyValueIdHash": ids["skuHash"],
        "provisioningBoundary": "DIRECT_SQL_FIXTURE_ONLY",
        "mutationBoundary": "AGENT_CONFIRM_ACTION_TO_JAVA_CANCEL_ORDER_API",
        "inventoryCovered": True,
        "initialStock": _INITIAL_STOCK,
        "expectedRestoreDelta": _ORDER_QUANTITY,
        "restoreBusinessKey": ids["restoreBusinessKey"],
        "compensationKey": ids["compensationKey"],
        "refundCovered": False,
        "cleanup": {"completed": False},
    }
    return ProvisionedAgentFixture(
        declared=declared,
        user_id=user_id,
        order_id=order_id,
        order_item_id=ids["orderItemId"],
        product_id=ids["productId"],
        sku_hash=ids["skuHash"],
        restore_business_key=ids["restoreBusinessKey"],
        compensation_key=ids["compensationKey"],
        capture_fixture=capture_fixture,
        template_values={"orderId": order_id},
        evidence=evidence,
    )


async def _provision_customer_service_order_fixture(
    declared: dict[str, Any],
    *,
    user_id: str,
    isolation_nonce: str | None = None,
) -> ProvisionedAgentFixture:
    """Create one isolated Java order snapshot for客服 read-path evaluation.

    This fixture deliberately provisions only the order-owned rows required by
    the internal Agent query APIs.  It is not a production data seeder and is
    guarded by the same local-only opt-in as write fixtures.
    """

    _fixture_guard(declared)
    nonce = isolation_nonce or secrets.token_hex(16)
    ids = _stable_customer_service_order_ids(user_id, nonce)
    order_status = int(declared.get("orderStatus", 1))
    if order_status not in {0, 1, 2, 3, 4, 5, 6, 7}:
        raise RuntimeError("customer-service order fixture orderStatus is invalid")
    item_status = int(declared.get("orderItemStatus", 1))
    amount = float(declared.get("amount", 199.0))
    subject = str(declared.get("subject") or "客服评测商品")[:200]
    product_name = str(declared.get("productName") or "客服评测商品")[:200]
    connection = await _connect_order_db(autocommit=False)
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO order_info
                    (order_id, amount, goods_amount, discount_amount, coupon_discount,
                     user_id, order_time, order_status, pay_channel, pay_scene,
                     pay_order_id, channel_order_Id, comment_status, subject)
                VALUES (%s, %s, %s, 0.00, 0.00, %s, NOW(3), %s,
                        %s, %s, %s, %s, %s, %s)
                """,
                (
                    ids["orderId"],
                    amount,
                    amount,
                    user_id,
                    order_status,
                    "alipay" if order_status != 0 else None,
                    "alipay_pc" if order_status != 0 else None,
                    f"EVPAY{ids['orderId'][-20:]}",
                    f"EVCHANNEL{ids['orderId'][-20:]}",
                    int(declared.get("commentStatus", 0)),
                    subject,
                ),
            )
            if int(cursor.rowcount or 0) != 1:
                raise RuntimeError("failed to provision exactly one客服 order")
            await cursor.execute(
                """
                INSERT INTO order_item
                    (order_item_id, order_id, product_id, product_name,
                     property_value_id_hash, property_info, item_amount,
                     buy_count, order_item_status, remark)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    ids["orderItemId"],
                    ids["orderId"],
                    ids["productId"],
                    product_name,
                    ids["skuHash"],
                    str(declared.get("propertyInfo") or "评测规格")[:150],
                    amount,
                    int(declared.get("buyCount", 1)),
                    item_status,
                    "LOCAL_EVALUATION_ONLY",
                ),
            )
            if int(cursor.rowcount or 0) != 1:
                raise RuntimeError("failed to provision exactly one客服 order item")
            if bool(declared.get("withLogistics")):
                await cursor.execute(
                    """
                    INSERT INTO order_logistics_info
                        (order_id, user_id, logistics_no, logistics_company,
                         sender_name, sender_phone, sender_address,
                         receiver_name, receiver_phone, receiver_address,
                         logistics_status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        ids["orderId"],
                        user_id,
                        f"EVLOG{ids['orderId'][-20:]}",
                        "LOCAL_EVALUATION_CARRIER",
                        "AI Shop evaluation",
                        None,
                        None,
                        "evaluation-user",
                        None,
                        None,
                        int(declared.get("logisticsStatus", 1)),
                    ),
                )
        await connection.commit()
    except BaseException:
        await connection.rollback()
        raise
    finally:
        connection.close()

    capture_fixture = {
        **declared,
        "kind": _CUSTOMER_SERVICE_ORDER,
        "scope": _LOCAL_SCOPE,
        "orderIds": [ids["orderId"]],
        "orderDatabaseAudit": {"enabled": False, "reason": "ORDER_ONLY_READ_FIXTURE"},
    }
    evidence = {
        "kind": _CUSTOMER_SERVICE_ORDER,
        "scope": _LOCAL_SCOPE,
        "sourceOrderId": declared.get("sourceOrderId"),
        "orderId": ids["orderId"],
        "orderItemId": ids["orderItemId"],
        "productId": ids["productId"],
        "orderStatus": order_status,
        "withLogistics": bool(declared.get("withLogistics")),
        "provisioningBoundary": "DIRECT_SQL_FIXTURE_ONLY",
        "mutationBoundary": "READ_ONLY_CUSTOMER_SERVICE_HTTP_EVALUATION",
        "cleanup": {"completed": False},
    }
    return ProvisionedAgentFixture(
        declared=declared,
        user_id=user_id,
        order_id=ids["orderId"],
        order_item_id=ids["orderItemId"],
        product_id=ids["productId"],
        sku_hash=ids["skuHash"],
        capture_fixture=capture_fixture,
        template_values={"orderId": ids["orderId"]},
        evidence=evidence,
        cleanup_mode="ORDER_ONLY",
    )


async def verify_write_fixture_prerequisites() -> dict[str, Any]:
    """Fail closed before a run if the local write evidence boundary is absent."""

    _fixture_guard({"scope": _LOCAL_SCOPE})
    connection = await _connect_order_db()
    try:
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT 1 FROM order_info LIMIT 1")
            await cursor.fetchone()
            await cursor.execute(
                """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE
                    (table_schema='aishop_order' AND table_name IN
                        ('order_info','order_item','order_request_idempotency',
                         'mq_compensation_log'))
                    OR
                    (table_schema='aishop_stock' AND table_name IN
                        ('sku_stock','stock_change_record'))
                """
            )
            row = await cursor.fetchone()
            table_count = int(row[0]) if row else 0
    finally:
        connection.close()
    if table_count != 6:
        raise RuntimeError("Java write fixture requires complete order and stock tables")
    return {
        "scope": _LOCAL_SCOPE,
        "databases": ["aishop_order", "aishop_stock"],
        "tables": [
            "aishop_order.order_info",
            "aishop_order.order_item",
            "aishop_order.order_request_idempotency",
            "aishop_order.mq_compensation_log",
            "aishop_stock.sku_stock",
            "aishop_stock.stock_change_record",
        ],
        "mutationPath": "AGENT_CONFIRM_ACTION_TO_JAVA_CANCEL_ORDER",
        "transactionBoundary": "SINGLE_MYSQL_TRANSACTION_ACROSS_SCHEMAS",
        "rollbackProbe": "NOT_RUN_BY_PREFLIGHT",
    }


async def capture_java_owned_order_state(
    user_id: str,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    """Read narrowly scoped Java-owned order, inventory, and ledger state."""

    audit = fixture.get("orderDatabaseAudit")
    if not isinstance(audit, dict) or audit.get("enabled") is not True:
        return {}
    _fixture_guard(fixture)
    order_ids = sorted({str(value) for value in fixture.get("orderIds") or [] if value})
    if not order_ids:
        raise RuntimeError("order database audit requires explicit orderIds")
    command_types = sorted(
        {str(value) for value in audit.get("commandTypes") or [] if value}
    )
    connection = await _connect_order_db()
    try:
        async with connection.cursor(aiomysql.DictCursor) as cursor:
            placeholders = ",".join(["%s"] * len(order_ids))
            await cursor.execute(
                f"""
                SELECT order_id, user_id, order_status, subject
                FROM order_info
                WHERE user_id=%s AND order_id IN ({placeholders})
                ORDER BY order_id
                """,
                (user_id, *order_ids),
            )
            orders = [dict(row) for row in await cursor.fetchall()]
            await cursor.execute(
                f"""
                SELECT order_item_id, order_id, product_id,
                       property_value_id_hash, buy_count, order_item_status
                FROM order_item
                WHERE order_id IN ({placeholders})
                ORDER BY order_item_id
                """,
                tuple(order_ids),
            )
            order_items = [dict(row) for row in await cursor.fetchall()]

            ledger_sql = """
                SELECT command_type, idempotency_key, request_hash, status
                FROM order_request_idempotency
                WHERE user_id=%s
            """
            params: tuple[Any, ...] = (user_id,)
            if command_types:
                command_placeholders = ",".join(["%s"] * len(command_types))
                ledger_sql += f" AND command_type IN ({command_placeholders})"
                params = (user_id, *command_types)
            ledger_sql += " ORDER BY command_type, idempotency_key"
            await cursor.execute(ledger_sql, params)
            ledger = [dict(row) for row in await cursor.fetchall()]

            inventory: list[dict[str, Any]] = []
            stock_changes: list[dict[str, Any]] = []
            compensation: list[dict[str, Any]] = []
            inventory_audit = audit.get("inventory")
            if isinstance(inventory_audit, dict):
                product_id = str(inventory_audit.get("productId") or "")
                sku_hash = str(inventory_audit.get("propertyValueIdHash") or "")
                restore_key = str(inventory_audit.get("restoreBusinessKey") or "")
                compensation_key = str(inventory_audit.get("compensationKey") or "")
                if not all((product_id, sku_hash, restore_key, compensation_key)):
                    raise RuntimeError("inventory audit identifiers are incomplete")
                await cursor.execute(
                    """
                    SELECT product_id, property_value_id_hash, stock
                    FROM aishop_stock.sku_stock
                    WHERE product_id=%s AND property_value_id_hash=%s
                    """,
                    (product_id, sku_hash),
                )
                inventory = [dict(row) for row in await cursor.fetchall()]
                await cursor.execute(
                    """
                    SELECT business_key, change_type, product_id,
                           property_value_id_hash, change_amount
                    FROM aishop_stock.stock_change_record
                    WHERE business_key=%s
                    ORDER BY business_key
                    """,
                    (restore_key,),
                )
                stock_changes = [dict(row) for row in await cursor.fetchall()]
                await cursor.execute(
                    """
                    SELECT idempotency_key, routing_key, status, retry_count
                    FROM mq_compensation_log
                    WHERE idempotency_key=%s
                    ORDER BY log_id
                    """,
                    (compensation_key,),
                )
                compensation = [dict(row) for row in await cursor.fetchall()]
    finally:
        connection.close()
    return {
        "orders": orders,
        "orderItems": order_items,
        "orderCommandLedger": ledger,
        "inventory": inventory,
        "stockChangeRecords": stock_changes,
        "compensationLogs": compensation,
        "counts": {
            "orders": len(orders),
            "orderItems": len(order_items),
            "orderCommandLedger": len(ledger),
            "inventory": len(inventory),
            "stockChangeRecords": len(stock_changes),
            "compensationLogs": len(compensation),
        },
        "source": "AUTHORITATIVE_JAVA_OWNED_MYSQL_READ",
    }
