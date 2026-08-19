from types import SimpleNamespace

from app.mcp_server import server as server_module


async def test_mcp_lifespan_starts_and_closes_episode_writer(monkeypatch):
    events: list[str] = []

    async def record(name: str) -> None:
        events.append(name)

    monkeypatch.setattr(
        server_module, "get_settings", lambda: SimpleNamespace(agent_auto_migrate=False)
    )
    monkeypatch.setattr(
        server_module.redis_service,
        "ensure_connected",
        lambda: record("redis.start"),
    )
    monkeypatch.setattr(server_module, "init_pool", lambda: record("db.start"))
    monkeypatch.setattr(
        server_module,
        "initialize_category_need_schemas",
        lambda: record("schema.start"),
    )
    monkeypatch.setattr(
        server_module.episode_service, "start", lambda: record("episode.start")
    )
    monkeypatch.setattr(
        server_module.episode_service, "close", lambda: record("episode.close")
    )
    monkeypatch.setattr(server_module, "close_pool", lambda: record("db.close"))
    monkeypatch.setattr(
        server_module.redis_service, "close", lambda: record("redis.close")
    )

    async with server_module._mcp_lifespan(None):
        events.append("serving")

    assert events == [
        "redis.start",
        "db.start",
        "schema.start",
        "episode.start",
        "serving",
        "episode.close",
        "db.close",
        "redis.close",
    ]
