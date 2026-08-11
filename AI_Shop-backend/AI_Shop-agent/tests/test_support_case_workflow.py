from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.services.mcp_tools_service import (
    propose_create_support_case,
    query_support_cases,
)
from app.services.support_case_service import SupportCaseService, support_case_service


class _Cursor:
    def __init__(self, *, rowcount: int = 1, lastrowid: int = 41):
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self.calls: list[tuple[str, tuple | list | None]] = []

    async def execute(self, sql, params=None):
        self.calls.append((" ".join(str(sql).split()), params))

    async def fetchone(self):
        return None

    async def fetchall(self):
        return []


def _acquire_for(cursor: _Cursor):
    @asynccontextmanager
    async def acquire():
        yield cursor

    return acquire


def test_category_mapping_keeps_after_sales_domains_deterministic():
    assert SupportCaseService.category_for_intent("ADDRESS_CHANGE") == "ADDRESS_CHANGE"
    assert SupportCaseService.category_for_intent("INVOICE") == "INVOICE"
    assert (
        SupportCaseService.category_for_intent("DAMAGED_OR_WRONG_ITEM", "包裹少了一件")
        == "MISSING_ITEM"
    )
    assert (
        SupportCaseService.category_for_intent("DAMAGED_OR_WRONG_ITEM", "发错颜色了")
        == "WRONG_ITEM"
    )


@pytest.mark.asyncio
async def test_build_proposal_carries_only_verified_image_evidence(monkeypatch):
    service = SupportCaseService()
    monkeypatch.setattr(
        "app.services.support_case_service.java_internal_client.get_order",
        AsyncMock(
            return_value={
                "order_id": "o1",
                "user_id": "u1",
                "order_status": 2,
            }
        ),
    )
    verify = AsyncMock(
        return_value={
            "approved": True,
            "asset_id": "img_0123456789abcdef0123456789abcdef",
            "content_sha256": "a" * 64,
            "mime_type": "image/jpeg",
            "width": 640,
            "height": 480,
            "scene": "agent",
            "moderation_status": "APPROVED",
            "expires_at": "2026-09-09T00:00:00",
        }
    )
    monkeypatch.setattr(
        "app.services.support_case_service.java_internal_client.verify_agent_image",
        verify,
    )

    proposal = await service.build_proposal(
        "u1",
        "DAMAGED",
        "包装破损",
        order_id="o1",
        image_asset_id="img_0123456789abcdef0123456789abcdef",
        image_understanding="包装边角有裂痕",
        image_understanding_status="FAILED",
        source_message_id=9,
        run_id="run-9",
    )

    assert proposal["ownedOrderValidated"] is True
    assert proposal["evidence"] == {
        "imageAssetId": "img_0123456789abcdef0123456789abcdef",
        "contentSha256": "a" * 64,
        "mimeType": "image/jpeg",
        "width": 640,
        "height": 480,
        "moderationStatus": "APPROVED",
        "scene": "agent",
        "expiresAt": "2026-09-09T00:00:00",
        "imageUnderstandingStatus": "FAILED",
        "imageUnderstanding": "包装边角有裂痕",
    }
    verify.assert_awaited_once_with("u1", "img_0123456789abcdef0123456789abcdef")


@pytest.mark.asyncio
async def test_unapproved_or_external_image_cannot_enter_case(monkeypatch):
    service = SupportCaseService()
    with pytest.raises(ValueError, match="资产标识"):
        await service.verify_image("u1", "https://evil.example/a.jpg")

    monkeypatch.setattr(
        "app.services.support_case_service.java_internal_client.verify_agent_image",
        AsyncMock(
            return_value={
                "approved": False,
                "asset_id": "img_abcdefabcdefabcdefabcdefabcdefab",
                "moderation_status": "REJECTED",
            }
        ),
    )
    with pytest.raises(ValueError, match="尚未通过审核"):
        await service.verify_image("u1", "img_abcdefabcdefabcdefabcdefabcdefab")


@pytest.mark.asyncio
async def test_create_is_idempotent_for_repeated_confirmation(monkeypatch):
    service = SupportCaseService()
    existing = {"caseId": 41, "caseNo": "SC2026080742ABCD", "status": "OPEN"}
    lookup = AsyncMock(return_value=existing)
    verify_owner = AsyncMock()
    monkeypatch.setattr(service, "get_by_idempotency", lookup)
    monkeypatch.setattr(service, "_verify_order_owner", verify_owner)
    cursor = _Cursor()

    with patch("app.services.support_case_service.acquire", _acquire_for(cursor)):
        result = await service.create(
            "u1",
            "DAMAGED",
            "包装破损",
            idempotency_key="act_case_1",
        )

    assert result == existing
    verify_owner.assert_not_awaited()
    assert cursor.calls == []


@pytest.mark.asyncio
async def test_normal_proposal_requires_confirmation_but_forced_handoff_creates_now(monkeypatch):
    proposal = {
        "category": "INVOICE",
        "description": "需要发票",
        "caseDedupeKey": "u1:invoice:9",
        "runId": "run-1",
        "sourceMessageId": 9,
        "priority": "NORMAL",
        "forcedHandoff": False,
    }
    pending = AsyncMock(return_value={"token": "act_case_1"})
    monkeypatch.setattr(support_case_service, "build_proposal", AsyncMock(return_value=proposal))
    monkeypatch.setattr(
        "app.services.support_case_service.pending_action_service.create_pending",
        pending,
    )

    reply = await propose_create_support_case("u1", "INVOICE", "需要发票", run_id="run-1")

    assert "确认卡片" in reply
    pending.assert_awaited_once()
    assert pending.await_args.args[:3] == (
        "CREATE_SUPPORT_CASE",
        "u1",
        proposal,
    )

    forced_case = {"caseNo": "SC20260807FORCED"}
    monkeypatch.setattr(
        support_case_service,
        "build_proposal",
        AsyncMock(return_value={**proposal, "forcedHandoff": True, "priority": "CRITICAL"}),
    )
    monkeypatch.setattr(support_case_service, "create", AsyncMock(return_value=forced_case))
    forced_reply = await propose_create_support_case(
        "u1", "PAYMENT_DISPUTE", "支付风险", forced_handoff=True
    )
    assert "立即转人工" in forced_reply
    support_case_service.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_query_support_cases_returns_scoped_cards_and_rejects_cross_user(monkeypatch):
    rows = [
        {
            "caseId": 1,
            "caseNo": "SC20260807ABC123",
            "status": "OPEN",
            "category": "LOGISTICS",
        }
    ]
    list_for_user = AsyncMock(side_effect=[rows, rows, []])
    monkeypatch.setattr(support_case_service, "list_for_user", list_for_user)

    listing = await query_support_cases("u1")
    detail = await query_support_cases("u1", "SC20260807ABC123")
    denied = await query_support_cases("u2", "SC20260807ABC123")

    assert json.loads(listing.assistant_cards)["type"] == "SUPPORT_CASE_LIST"
    assert json.loads(detail.assistant_cards)["type"] == "SUPPORT_CASE_DETAIL"
    assert denied.success is False
    assert denied.error_code == "NOT_FOUND"
    assert list_for_user.await_args_list[0].args == ("u1", None)
    assert list_for_user.await_args_list[1].args == ("u1", "SC20260807ABC123")
