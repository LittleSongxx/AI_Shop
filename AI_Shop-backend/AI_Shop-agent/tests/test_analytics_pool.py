import asyncio
import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pymysql
import pytest

from app.services.analytics_catalog import CATALOG


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Cursor:
    def __init__(self, row=None):
        self.executed: list[str] = []
        self.row = row

    async def execute(self, sql: str, params=None) -> None:
        self.executed.append((sql, params) if params is not None else sql)

    async def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, cursor: _Cursor):
        self._cursor = cursor
        self.rollback = AsyncMock()

    def cursor(self, *_args):
        return _AsyncContext(self._cursor)


class _Pool:
    def __init__(self, connection: _Connection):
        self._connection = connection
        self.closed = False
        self.wait_closed = AsyncMock()

    def acquire(self):
        return _AsyncContext(self._connection)

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_init_analytics_pool_checks_every_catalog_view(monkeypatch):
    from app.db import analytics_pool as module

    cursor = _Cursor()
    pool = _Pool(_Connection(cursor))
    settings = SimpleNamespace(
        data_analyst_enabled=True,
        analytics_mysql_user="analytics_reader",
        analytics_mysql_password="separate-secret",
        analytics_mysql_database="aishop_admin",
        analytics_mysql_host="localhost",
        analytics_mysql_port=3306,
        analytics_mysql_pool_min_size=1,
        analytics_mysql_pool_max_size=2,
        mysql_pool_recycle_seconds=3600,
        mysql_user="business_user",
    )
    create_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module.aiomysql, "create_pool", create_pool)
    monkeypatch.setattr(module, "_analytics_pool", None)

    try:
        await module.init_analytics_pool()

        assert cursor.executed == [f"SELECT 1 FROM `{view}` LIMIT 0" for view in CATALOG]
        assert module._analytics_pool is pool
    finally:
        await module.close_analytics_pool()

    assert pool.closed is True
    pool.wait_closed.assert_awaited_once()


@pytest.mark.asyncio
async def test_acquire_analytics_applies_evaluation_clock_per_connection(monkeypatch):
    from app.db import analytics_pool as module

    cursor = _Cursor()
    pool = _Pool(_Connection(cursor))
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(analytics_eval_fixed_now="2026-08-27 12:00:00"),
    )
    monkeypatch.setattr(module, "_analytics_pool", pool)

    try:
        async with module.acquire_analytics() as acquired:
            assert acquired is cursor
    finally:
        monkeypatch.setattr(module, "_analytics_pool", None)

    assert cursor.executed == [
        "SET SESSION time_zone = '+08:00'",
        (
            "SET SESSION timestamp = UNIX_TIMESTAMP(%s)",
            ("2026-08-27 12:00:00",),
        ),
    ]


@pytest.mark.asyncio
async def test_acquire_snapshot_uses_one_read_only_repeatable_read_transaction(monkeypatch):
    from app.db import analytics_pool as module

    cursor = _Cursor({"data_as_of": datetime(2026, 8, 27, 12, 0, 0, 123456)})
    connection = _Connection(cursor)
    pool = _Pool(connection)
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(analytics_eval_fixed_now="2026-08-27 12:00:00"),
    )
    monkeypatch.setattr(module, "_analytics_pool", pool)

    try:
        async with module.acquire_analytics_snapshot() as snapshot:
            assert snapshot.cursor is cursor
            assert snapshot.data_as_of == "2026-08-27T12:00:00.123456+08:00"
    finally:
        monkeypatch.setattr(module, "_analytics_pool", None)

    assert cursor.executed == [
        "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ",
        "SET SESSION time_zone = '+08:00'",
        (
            "SET SESSION timestamp = UNIX_TIMESTAMP(%s)",
            ("2026-08-27 12:00:00",),
        ),
        "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY",
        "SELECT NOW(6) AS data_as_of",
    ]
    assert connection.rollback.await_count == 2


@pytest.mark.mysql
@pytest.mark.skipif(
    os.getenv("TEXT2SQL_EVAL_MYSQL_TESTS") != "1",
    reason="set TEXT2SQL_EVAL_MYSQL_TESTS=1 after fixture-bootstrap",
)
@pytest.mark.asyncio
async def test_real_snapshot_is_stable_across_concurrent_source_update(monkeypatch):
    from app.db import analytics_pool as module
    from app.services.data_analyst_service import _execute_sql, _explain_sql

    settings = SimpleNamespace(
        data_analyst_enabled=True,
        analytics_mysql_user="text2sql_reader",
        analytics_mysql_password="text2sql-reader-local-only",
        analytics_mysql_database="aishop_admin",
        analytics_mysql_host="127.0.0.1",
        analytics_mysql_port=13316,
        analytics_mysql_pool_min_size=1,
        analytics_mysql_pool_max_size=2,
        mysql_pool_recycle_seconds=3600,
        mysql_user="business_user",
        analytics_eval_fixed_now="2026-08-27 12:00:00",
    )
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    await module.close_analytics_pool()
    await module.init_analytics_pool()
    sql = (
        "SELECT stock FROM analytics_inventory_risk "
        "WHERE product_id = 'P100' AND property_value_id_hash = 'SKU-P100-A' LIMIT 1"
    )

    def set_stock(value: int) -> None:
        connection = pymysql.connect(
            host="127.0.0.1",
            port=13316,
            user="root",
            password="text2sql-root-local-only",
            database="aishop_stock",
            autocommit=True,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE sku_stock SET stock=%s "
                    "WHERE product_id='P100' AND property_value_id_hash='SKU-P100-A'",
                    (value,),
                )
        finally:
            connection.close()

    try:
        await asyncio.to_thread(set_stock, 0)
        async with module.acquire_analytics_snapshot() as snapshot:
            first = await _execute_sql(sql, 3000, snapshot.cursor)
            with pytest.raises(Exception) as explain_failure:
                await _explain_sql(sql, 3000, snapshot.cursor)
            await asyncio.to_thread(set_stock, 123)
            second = await _execute_sql(sql, 3000, snapshot.cursor)
            assert snapshot.data_as_of.startswith("2026-08-27T12:00:00")
            assert explain_failure.value.args[0] == 1345
            assert first == second == [{"stock": 0}]

        async with module.acquire_analytics_snapshot() as next_snapshot:
            latest = await _execute_sql(sql, 3000, next_snapshot.cursor)
        assert latest == [{"stock": 123}]
    finally:
        await asyncio.to_thread(set_stock, 0)
        await module.close_analytics_pool()
