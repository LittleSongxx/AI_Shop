from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from app.services.analytics_result_service import AnalyticsResultError


class _AtomicClarificationRedis:
    def __init__(self) -> None:
        self.values: dict[str, dict] = {}
        self.ttls: list[int] = []

    @property
    def client(self):
        return self

    async def set_json(self, key: str, value: dict, ttl: int) -> None:
        self.values[key] = copy.deepcopy(value)
        self.ttls.append(ttl)

    async def eval(
        self, _script: str, _key_count: int, key: str, scope: str, choice: str, now: int
    ):
        payload = self.values.get(key)
        if payload is None:
            return ["MISSING"]
        if payload.get("ownerScopeHash") != scope:
            return ["OWNER"]
        if int(payload.get("expiresAt") or 0) < now:
            self.values.pop(key, None)
            return ["EXPIRED"]
        if choice not in {
            str(option.get("choiceId") or "") for option in payload.get("options") or []
        }:
            return ["CHOICE"]
        self.values.pop(key, None)
        return ["OK", json.dumps(payload, ensure_ascii=False)]


def _options() -> list[dict[str, str]]:
    return [
        {
            "choiceId": "paid_units",
            "label": "按支付件数",
            "answerSuffix": "按支付件数排序",
        },
        {
            "choiceId": "paid_amount",
            "label": "按支付金额",
            "answerSuffix": "按支付金额排序",
        },
    ]


@pytest.mark.asyncio
async def test_clarification_token_is_owner_bound_ttl_and_atomically_one_use(monkeypatch):
    from app.services import analytics_clarification_service as module

    memory = _AtomicClarificationRedis()
    monkeypatch.setattr(module, "redis_service", memory)
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(analytics_cursor_ttl_seconds=900),
    )
    service = module.AnalyticsClarificationService()
    permissions = {"analytics:read"}
    issued = await service.issue(
        question="最近最好卖的商品",
        clarification_question="按什么口径？",
        options=_options(),
        admin_id="admin-a",
        permissions=permissions,
        tenant_id=None,
        run_id="run-parent",
    )

    assert issued["clarificationTokenTtlSeconds"] == 900
    assert issued["clarificationOptions"] == _options()
    with pytest.raises(AnalyticsResultError) as owner:
        await service.consume(
            issued["clarificationToken"],
            "paid_units",
            admin_id="admin-b",
            permissions=permissions,
            tenant_id=None,
        )
    assert owner.value.code == "CLARIFICATION_OWNER_MISMATCH"

    resolved = await service.consume(
        issued["clarificationToken"],
        "paid_units",
        admin_id="admin-a",
        permissions=permissions,
        tenant_id=None,
    )
    assert "按支付件数排序" in resolved["resolvedQuestion"]
    assert resolved["parentRunId"] == "run-parent"

    with pytest.raises(AnalyticsResultError) as replay:
        await service.consume(
            issued["clarificationToken"],
            "paid_units",
            admin_id="admin-a",
            permissions=permissions,
            tenant_id=None,
        )
    assert replay.value.code == "CLARIFICATION_TOKEN_EXPIRED"
    assert replay.value.http_status == 410


@pytest.mark.asyncio
async def test_invalid_choice_does_not_consume_clarification_token(monkeypatch):
    from app.services import analytics_clarification_service as module

    memory = _AtomicClarificationRedis()
    monkeypatch.setattr(module, "redis_service", memory)
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(analytics_cursor_ttl_seconds=900),
    )
    service = module.AnalyticsClarificationService()
    issued = await service.issue(
        question="最近最好卖的商品",
        clarification_question="按什么口径？",
        options=_options(),
        admin_id="admin-a",
        permissions={"analytics:read"},
        tenant_id=None,
        run_id="run-parent",
    )

    with pytest.raises(AnalyticsResultError) as invalid:
        await service.consume(
            issued["clarificationToken"],
            "unknown",
            admin_id="admin-a",
            permissions={"analytics:read"},
            tenant_id=None,
        )
    assert invalid.value.code == "CLARIFICATION_CHOICE_INVALID"

    resolved = await service.consume(
        issued["clarificationToken"],
        "paid_amount",
        admin_id="admin-a",
        permissions={"analytics:read"},
        tenant_id=None,
    )
    assert resolved["choice"]["choiceId"] == "paid_amount"
