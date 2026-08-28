from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiomysql

from app.config.settings import get_settings
from app.services.analytics_catalog import CATALOG

_analytics_pool: aiomysql.Pool | None = None


@dataclass(frozen=True)
class AnalyticsSnapshot:
    cursor: Any
    data_as_of: str


async def init_analytics_pool() -> None:
    global _analytics_pool
    settings = get_settings()
    if not settings.data_analyst_enabled or _analytics_pool is not None:
        return
    if not settings.analytics_mysql_user.strip() or not settings.analytics_mysql_password.strip():
        raise RuntimeError("dedicated analytics database credentials are required")
    if settings.analytics_mysql_user.strip().lower() in {
        "root",
        settings.mysql_user.strip().lower(),
    }:
        raise RuntimeError("DataAnalyst cannot reuse the business database identity")
    if settings.analytics_mysql_database.strip().lower() != "aishop_admin":
        raise RuntimeError("DataAnalyst must connect to the governed aishop_admin schema")
    pool = await aiomysql.create_pool(
        host=settings.analytics_mysql_host,
        port=settings.analytics_mysql_port,
        user=settings.analytics_mysql_user,
        password=settings.analytics_mysql_password,
        db=settings.analytics_mysql_database,
        charset="utf8mb4",
        autocommit=True,
        minsize=settings.analytics_mysql_pool_min_size,
        maxsize=settings.analytics_mysql_pool_max_size,
        pool_recycle=settings.mysql_pool_recycle_seconds,
        init_command="SET SESSION TRANSACTION READ ONLY",
    )
    try:
        async with pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for view in CATALOG:
                    await cursor.execute(f"SELECT 1 FROM `{view}` LIMIT 0")
    except Exception:
        pool.close()
        await pool.wait_closed()
        raise RuntimeError("governed analytics views are unavailable") from None
    _analytics_pool = pool


async def close_analytics_pool() -> None:
    global _analytics_pool
    if _analytics_pool is not None:
        _analytics_pool.close()
        await _analytics_pool.wait_closed()
        _analytics_pool = None


@asynccontextmanager
async def acquire_analytics():
    if _analytics_pool is None:
        raise RuntimeError("analytics DB pool not initialized")
    async with _analytics_pool.acquire() as connection:
        async with connection.cursor(aiomysql.DictCursor) as cursor:
            fixed_now = get_settings().analytics_eval_fixed_now.strip()
            if fixed_now:
                await cursor.execute("SET SESSION time_zone = '+08:00'")
                await cursor.execute("SET SESSION timestamp = UNIX_TIMESTAMP(%s)", (fixed_now,))
            yield cursor


@asynccontextmanager
async def acquire_analytics_snapshot():
    """Acquire one read-only repeatable-read snapshot for the whole request.

    EXPLAIN and every metric branch must use the yielded cursor sequentially.
    The rollback in ``finally`` also runs for model/query timeouts and task
    cancellation, so a pooled connection cannot leak a snapshot to its next
    borrower.
    """

    if _analytics_pool is None:
        raise RuntimeError("analytics DB pool not initialized")
    async with _analytics_pool.acquire() as connection:
        try:
            await connection.rollback()
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                settings = get_settings()
                await cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                await cursor.execute("SET SESSION time_zone = '+08:00'")
                fixed_now = settings.analytics_eval_fixed_now.strip()
                if fixed_now:
                    await cursor.execute("SET SESSION timestamp = UNIX_TIMESTAMP(%s)", (fixed_now,))
                else:
                    await cursor.execute("SET SESSION timestamp = DEFAULT")
                await cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                await cursor.execute("SELECT NOW(6) AS data_as_of")
                row = await cursor.fetchone()
                value = (row or {}).get("data_as_of") if isinstance(row, dict) else None
                if isinstance(value, datetime):
                    data_as_of = value.isoformat(timespec="microseconds") + "+08:00"
                else:
                    data_as_of = str(value or "")
                yield AnalyticsSnapshot(cursor=cursor, data_as_of=data_as_of)
        finally:
            await connection.rollback()
