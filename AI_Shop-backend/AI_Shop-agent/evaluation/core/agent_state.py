"""Authoritative Java-owned state snapshots for repeated Agent evaluation."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from app.services.java_internal_client import delegated_user_scope, java_internal_client
from evaluation.core.agent_fixtures import capture_java_owned_order_state


def _stable_rows(rows: Any, *identity_keys: str) -> list[dict[str, Any]]:
    values = [dict(row) for row in rows or [] if isinstance(row, Mapping)]
    return sorted(
        values,
        key=lambda row: tuple(str(row.get(key) or "") for key in identity_keys),
    )


async def capture_authoritative_state(
    user_id: str,
    fixture: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read state from Java APIs without inferring success from Agent output."""

    fixture = fixture or {}
    product_ids = [str(item) for item in fixture.get("productIds") or [] if str(item)]
    with delegated_user_scope(user_id):
        calls: list[tuple[str, Any]] = [
            ("orders", java_internal_client.list_orders(user_id, limit=100)),
            ("coupons", java_internal_client.list_user_coupons(user_id)),
        ]
        if product_ids:
            calls.append(
                ("offers", java_internal_client.offer_snapshot_batch(user_id, product_ids))
            )
        results = await asyncio.gather(*(call for _name, call in calls), return_exceptions=True)
    state: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    for (name, _call), result in zip(calls, results):
        if isinstance(result, BaseException):
            errors.append({"component": name, "type": type(result).__name__})
            continue
        if name == "orders":
            state[name] = _stable_rows(result, "order_id", "id")
        elif name == "coupons":
            state[name] = _stable_rows(result, "user_coupon_id", "coupon_id", "id")
        elif name == "offers":
            rows = result.get("products") if isinstance(result, Mapping) else []
            state[name] = _stable_rows(rows, "product_id", "productId")
    order_audit: dict[str, Any] = {}
    if isinstance(fixture, Mapping) and fixture.get("orderDatabaseAudit"):
        try:
            order_audit = await capture_java_owned_order_state(user_id, dict(fixture))
        except BaseException as exc:
            errors.append({"component": "orderCommandLedger", "type": type(exc).__name__})
    return {
        "available": not errors,
        "components": state,
        "orderAudit": order_audit,
        "errors": errors,
        "source": "AUTHORITATIVE_JAVA_INTERNAL_APIS_AND_JAVA_OWNED_LEDGER",
    }
