from __future__ import annotations

from contextlib import asynccontextmanager

import aiomysql

from app.config.settings import get_settings

_analytics_pool: aiomysql.Pool | None = None


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
        minsize=1,
        maxsize=4,
        init_command="SET SESSION TRANSACTION READ ONLY",
    )
    try:
        async with pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for view in (
                    "analytics_sales_daily",
                    "analytics_product_sales_daily",
                    "analytics_inventory_risk",
                    "analytics_agent_quality_daily",
                    "analytics_tool_quality_daily",
                ):
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
            yield cursor
