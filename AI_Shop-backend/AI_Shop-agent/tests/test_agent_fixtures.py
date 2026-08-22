from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db import pool as db_pool
from app.utils.order_ids import extract_order_id, extract_order_item_id
from evaluation.core import agent_fixtures


class _FakeCursor:
    def __init__(self, *, fail_order_item_insert: bool = False) -> None:
        self.fail_order_item_insert = fail_order_item_insert
        self.statements: list[tuple[str, tuple | None]] = []
        self.last_sql = ""
        self.rowcount = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql: str, params: tuple | None = None) -> None:
        normalized = " ".join(sql.split())
        self.last_sql = normalized
        self.statements.append((normalized, params))
        if normalized.startswith("SELECT"):
            self.rowcount = 0
        elif self.fail_order_item_insert and normalized.startswith("INSERT INTO order_item"):
            self.rowcount = 0
        else:
            self.rowcount = 1

    async def fetchone(self):
        if "SELECT order_status" in self.last_sql:
            return (0,)
        if "SELECT stock FROM aishop_stock.sku_stock" in self.last_sql:
            return (agent_fixtures._INITIAL_STOCK,)
        if "AS residual" in self.last_sql:
            return {"residual": 0}
        if "SELECT COUNT(*)" in self.last_sql:
            return (0,)
        return None

    async def fetchall(self):
        return []


class _FakeConnection:
    def __init__(self, *, fail_order_item_insert: bool = False) -> None:
        self.fake_cursor = _FakeCursor(fail_order_item_insert=fail_order_item_insert)
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.closed = False

    def cursor(self, *_args, **_kwargs):
        return self.fake_cursor

    def close(self) -> None:
        self.closed = True


def _enable_local_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_fixtures,
        "get_settings",
        lambda: SimpleNamespace(app_env="development"),
    )
    monkeypatch.setenv("AI_EVAL_ENABLE_WRITE_FIXTURES", "true")


def test_write_fixture_requires_explicit_local_enablement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_fixtures,
        "get_settings",
        lambda: SimpleNamespace(app_env="development"),
    )
    monkeypatch.delenv("AI_EVAL_ENABLE_WRITE_FIXTURES", raising=False)
    with pytest.raises(RuntimeError, match="AI_EVAL_ENABLE_WRITE_FIXTURES"):
        agent_fixtures._fixture_guard({"scope": "LOCAL_EVALUATION_ONLY"})


def test_write_fixture_is_forbidden_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_fixtures,
        "get_settings",
        lambda: SimpleNamespace(app_env="production"),
    )
    monkeypatch.setenv("AI_EVAL_ENABLE_WRITE_FIXTURES", "true")
    with pytest.raises(RuntimeError, match="forbidden in production"):
        agent_fixtures._fixture_guard({"scope": "LOCAL_EVALUATION_ONLY"})


def test_write_fixture_requires_exact_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_fixtures,
        "get_settings",
        lambda: SimpleNamespace(app_env="development"),
    )
    monkeypatch.setenv("AI_EVAL_ENABLE_WRITE_FIXTURES", "true")
    with pytest.raises(RuntimeError, match="LOCAL_EVALUATION_ONLY"):
        agent_fixtures._fixture_guard({"scope": "shared"})


@pytest.mark.asyncio
async def test_unknown_fixture_kind_fails_before_database_access() -> None:
    with pytest.raises(RuntimeError, match="unsupported Agent state fixture kind"):
        await agent_fixtures.provision_agent_fixture(
            {"kind": "UNSAFE_GENERIC_ORDER", "scope": "LOCAL_EVALUATION_ONLY"},
            user_id="evfixturetest01",
        )


def test_java_session_payload_is_exact_and_local_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_local_fixture(monkeypatch)

    payload = agent_fixtures.build_java_web_session_payload(
        {"kind": "CANCELABLE_ORDER_V1", "scope": "LOCAL_EVALUATION_ONLY"},
        user_id="evfixturetest1",
        token="evaluation-token",
    )

    assert payload == {
        "@class": "com.aishop.entity.dto.TokenUserInfoDTO",
        "userId": "evfixturetest1",
        "email": None,
        "nickName": "evaluation",
        "avatar": None,
        "token": "evaluation-token",
    }


def test_fixture_order_id_matches_java_production_shape_and_is_stable() -> None:
    first = agent_fixtures._stable_fixture_ids("evfixturetest1", "stable-trial")
    second = agent_fixtures._stable_fixture_ids("evfixturetest1", "stable-trial")

    assert first == second
    assert len(first["orderId"]) == 32
    assert first["orderId"][:17].isdigit()
    assert first["orderId"][17:].isalnum()
    assert extract_order_id(f"取消订单 {first['orderId']}") == first["orderId"]
    assert (
        extract_order_item_id(f"订单项 {first['orderItemId']}")
        == first["orderItemId"]
    )


def test_java_session_payload_is_forbidden_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_fixtures,
        "get_settings",
        lambda: SimpleNamespace(app_env="production"),
    )
    monkeypatch.setenv("AI_EVAL_ENABLE_WRITE_FIXTURES", "true")

    with pytest.raises(RuntimeError, match="forbidden in production"):
        agent_fixtures.build_java_web_session_payload(
            {"kind": "CANCELABLE_ORDER_V1", "scope": "LOCAL_EVALUATION_ONLY"},
            user_id="evfixturetest1",
            token="evaluation-token",
        )


@pytest.mark.asyncio
async def test_fixture_provisions_and_cleans_complete_order_stock_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_local_fixture(monkeypatch)
    provision_connection = _FakeConnection()
    cleanup_connection = _FakeConnection()
    connections = [provision_connection, cleanup_connection]

    async def connect(*, autocommit: bool = True):
        assert autocommit is False
        return connections.pop(0)

    agent_cursor = _FakeCursor()

    @asynccontextmanager
    async def transaction():
        yield agent_cursor

    monkeypatch.setattr(agent_fixtures, "_connect_order_db", connect)
    monkeypatch.setattr(db_pool, "transaction", transaction)

    fixture = await agent_fixtures.provision_agent_fixture(
        {"kind": "CANCELABLE_ORDER_V1", "scope": "LOCAL_EVALUATION_ONLY"},
        user_id="evfixturetest1",
        isolation_nonce="unit-test",
    )
    provision_sql = [row[0] for row in provision_connection.fake_cursor.statements]
    assert any(sql.startswith("INSERT INTO aishop_stock.sku_stock") for sql in provision_sql)
    assert any(sql.startswith("INSERT INTO order_info") for sql in provision_sql)
    assert any(sql.startswith("INSERT INTO order_item") for sql in provision_sql)
    provision_connection.commit.assert_awaited_once()
    provision_connection.rollback.assert_not_awaited()
    assert fixture.evidence["inventoryCovered"] is True

    await fixture.cleanup()

    cleanup_sql = [row[0] for row in cleanup_connection.fake_cursor.statements]
    order_item_delete = next(
        index for index, sql in enumerate(cleanup_sql) if sql.startswith("DELETE FROM order_item")
    )
    order_delete = next(
        index for index, sql in enumerate(cleanup_sql) if sql.startswith("DELETE FROM order_info")
    )
    assert order_item_delete < order_delete
    assert any("order_request_idempotency" in sql for sql in cleanup_sql)
    assert any("mq_compensation_log" in sql for sql in cleanup_sql)
    assert any("aishop_stock.stock_change_record" in sql for sql in cleanup_sql)
    assert any("aishop_stock.sku_stock" in sql for sql in cleanup_sql)
    cleanup_connection.commit.assert_awaited_once()
    cleanup_connection.rollback.assert_not_awaited()
    assert fixture.evidence["cleanup"]["completed"] is True


@pytest.mark.asyncio
async def test_fixture_provision_rolls_back_when_order_item_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_local_fixture(monkeypatch)
    connection = _FakeConnection(fail_order_item_insert=True)

    async def connect(*, autocommit: bool = True):
        assert autocommit is False
        return connection

    monkeypatch.setattr(agent_fixtures, "_connect_order_db", connect)

    with pytest.raises(RuntimeError, match="order item"):
        await agent_fixtures.provision_agent_fixture(
            {"kind": "CANCELABLE_ORDER_V1", "scope": "LOCAL_EVALUATION_ONLY"},
            user_id="evfixturetest1",
            isolation_nonce="rollback-test",
        )

    connection.rollback.assert_awaited_once()
    connection.commit.assert_not_awaited()
    assert connection.closed is True
