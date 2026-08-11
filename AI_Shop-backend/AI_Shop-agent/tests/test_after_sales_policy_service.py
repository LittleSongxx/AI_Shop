from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.after_sales_policy_service import (
    CONFLICT,
    ELIGIBLE,
    INELIGIBLE,
    NEEDS_EVIDENCE,
    POLICY_UNAVAILABLE,
    AfterSalesPolicyService,
)


class _Cursor:
    def __init__(self, rows: list[dict] | None = None):
        self.rows = rows or []
        self.calls: list[tuple[str, object]] = []

    async def execute(self, sql: str, params=None):
        self.calls.append((" ".join(sql.split()), params))

    async def fetchall(self):
        return self.rows


def _acquire_for(cursor: _Cursor):
    @asynccontextmanager
    async def acquire():
        yield cursor

    return acquire


def _policy(
    *,
    policy_id: str = "global-refund",
    priority: int = 0,
    scope: dict | None = None,
    rule: dict | None = None,
) -> dict:
    return {
        "policy_id": policy_id,
        "version": "v1",
        "priority": priority,
        "scope_json": scope or {"scopeType": "GLOBAL"},
        "rule_json": rule
        or {
            "action": "REFUND",
            "orderStatuses": [1, 2, 7],
            "itemStatuses": [1],
            "requiredEvidence": [],
        },
        "effective_start": datetime(2020, 1, 1, tzinfo=timezone.utc),
        "effective_end": None,
    }


def _order(*, user_id: str = "u1", status: int = 1) -> dict:
    return {
        "order_id": "o1",
        "user_id": user_id,
        "order_status": status,
        "order_time": datetime.now(timezone.utc).isoformat(),
    }


def _item(*, status: int = 1) -> dict:
    return {
        "order_id": "o1",
        "order_item_id": "i1",
        "order_item_status": status,
        "product_id": "p1",
        "property_value_id_hash": "sku-1",
    }


async def _evaluate(
    *,
    policies: list[dict],
    order: dict | None = None,
    item: dict | None = None,
    evidence: list[str] | None = None,
) -> tuple[dict, _Cursor]:
    cursor = _Cursor(policies)
    service = AfterSalesPolicyService()
    with (
        patch(
            "app.services.after_sales_policy_service.get_settings",
            return_value=SimpleNamespace(after_sales_policy_engine_enabled=True),
        ),
        patch(
            "app.services.after_sales_policy_service.acquire",
            _acquire_for(cursor),
        ),
        patch(
            "app.services.after_sales_policy_service.java_internal_client.get_order_item",
            AsyncMock(return_value=item or _item()),
        ),
        patch(
            "app.services.after_sales_policy_service.java_internal_client.get_order",
            AsyncMock(return_value=order or _order()),
        ),
        patch(
            "app.services.after_sales_policy_service.java_internal_client.get_product_detail",
            AsyncMock(return_value={"category_id": "c1"}),
        ),
        patch(
            "app.services.after_sales_policy_service.java_internal_client.send_user_notification",
            AsyncMock(),
        ),
    ):
        result = await service.evaluate(
            user_id="u1",
            action="REFUND",
            order_id="o1",
            order_item_id="i1",
            evidence=evidence,
        )
    return result, cursor


def test_sku_rule_overrides_product_category_and_global_rules():
    service = AfterSalesPolicyService()
    facts = {"skuKey": "sku-1", "productId": "p1", "categoryId": "c1"}
    rows = [
        {**_policy(policy_id="global"), "scope": {"scopeType": "GLOBAL"}, "rule": {"x": 0}},
        {**_policy(policy_id="category"), "scope": {"scopeType": "CATEGORY", "scopeId": "c1"}, "rule": {"x": 1}},
        {**_policy(policy_id="product"), "scope": {"scopeType": "PRODUCT", "scopeId": "p1"}, "rule": {"x": 2}},
        {**_policy(policy_id="sku"), "scope": {"scopeType": "SKU", "scopeId": "sku-1"}, "rule": {"x": 3}},
    ]

    selected, conflict = service._select_policy(rows, facts)

    assert not conflict
    assert selected is not None
    assert selected[0]["policy_id"] == "sku"


def test_same_specificity_and_priority_with_different_rules_conflicts():
    service = AfterSalesPolicyService()
    rows = [
        {**_policy(policy_id="a"), "scope": {"scopeType": "GLOBAL"}, "rule": {"x": 1}},
        {**_policy(policy_id="b"), "scope": {"scopeType": "GLOBAL"}, "rule": {"x": 2}},
    ]

    selected, conflict = service._select_policy(rows, {})

    assert selected is None
    assert conflict


@pytest.mark.asyncio
async def test_no_published_rule_returns_policy_unavailable():
    result, _cursor = await _evaluate(policies=[])

    assert result["decision"] == POLICY_UNAVAILABLE
    assert result["decisionId"].startswith("after_sales_")


@pytest.mark.asyncio
async def test_order_owner_mismatch_returns_conflict():
    result, _cursor = await _evaluate(policies=[_policy()], order=_order(user_id="u2"))

    assert result["decision"] == CONFLICT
    assert "不属于当前用户" in result["reason"]


@pytest.mark.asyncio
async def test_disallowed_order_or_item_status_returns_ineligible():
    order_result, _cursor = await _evaluate(policies=[_policy()], order=_order(status=5))
    item_result, _cursor = await _evaluate(policies=[_policy()], item=_item(status=0))

    assert order_result["decision"] == INELIGIBLE
    assert item_result["decision"] == INELIGIBLE


@pytest.mark.asyncio
async def test_missing_required_image_returns_needs_evidence():
    policy = _policy(
        rule={
            "action": "REFUND",
            "orderStatuses": [1],
            "itemStatuses": [1],
            "requiredEvidence": ["IMAGE"],
        }
    )

    result, _cursor = await _evaluate(policies=[policy])

    assert result["decision"] == NEEDS_EVIDENCE
    assert result["missingEvidence"] == ["IMAGE"]


@pytest.mark.asyncio
async def test_missing_evidence_uses_existing_notification_path():
    cursor = _Cursor()
    notify = AsyncMock()
    service = AfterSalesPolicyService()
    result = service._result(
        NEEDS_EVIDENCE,
        action="REFUND",
        order_id="o1",
        order_item_id="i1",
        missing_evidence=["IMAGE"],
    )
    with (
        patch("app.services.after_sales_policy_service.acquire", _acquire_for(cursor)),
        patch(
            "app.services.after_sales_policy_service.java_internal_client.send_user_notification",
            notify,
        ),
    ):
        finished = await service._finish(result, "u1")

    notify.assert_awaited_once()
    assert notify.await_args.kwargs["biz_type"] == "after_sales_evidence"
    assert notify.await_args.kwargs["biz_id"] == finished["decisionId"]


@pytest.mark.asyncio
async def test_expired_window_returns_ineligible():
    old_order = _order()
    old_order["order_time"] = (
        datetime.now(timezone.utc) - timedelta(days=31)
    ).isoformat()
    policy = _policy(
        rule={
            "action": "REFUND",
            "orderStatuses": [1],
            "itemStatuses": [1],
            "windowDays": 7,
        }
    )

    result, _cursor = await _evaluate(policies=[policy], order=old_order)

    assert result["decision"] == INELIGIBLE
    assert "时间窗口" in result["reason"]


@pytest.mark.asyncio
async def test_eligible_result_has_version_facts_and_is_persisted():
    result, cursor = await _evaluate(policies=[_policy()], evidence=["IMAGE"])

    assert result["decision"] == ELIGIBLE
    assert result["policyId"] == "global-refund"
    assert result["policyVersion"] == "v1"
    assert result["factReferences"]["orderId"] == "o1"
    assert result["decisionId"].startswith("after_sales_")
    assert any("INSERT INTO agent_after_sales_eligibility" in sql for sql, _ in cursor.calls)
