"""Shared httpx.AsyncClient instances.

Building an ``AsyncClient`` per request discards its connection pool on exit, so
every call pays a fresh TCP (and TLS) handshake. httpx's own guidance is to keep
a long-lived client and reuse it. Clients here are created lazily, keyed by
purpose so each dependency keeps an independent pool, and closed on shutdown.

Clients are additionally keyed by the running event loop: an ``AsyncClient``
binds its pool to the loop that first used it, so reusing one across loops (a
worker process, or successive test loops) would fail. A new loop transparently
gets a new client instead.
"""

from __future__ import annotations

import asyncio
import threading

import httpx
import structlog

logger = structlog.get_logger()

DEFAULT_LIMITS = httpx.Limits(
    max_connections=100,
    max_keepalive_connections=20,
    keepalive_expiry=30.0,
)

_clients: dict[tuple[str, int], httpx.AsyncClient] = {}

# A sync lock, deliberately: client construction never awaits, and a module-level
# asyncio.Lock would bind itself to the first event loop that awaited it and then
# raise on every other loop.
_lock = threading.Lock()


def _loop_key(name: str) -> tuple[str, int]:
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0
    return (name, loop_id)


async def get_client(
    name: str,
    *,
    timeout: float = 30.0,
    limits: httpx.Limits | None = None,
) -> httpx.AsyncClient:
    """Return the shared client for ``name``, creating it on first use.

    ``timeout`` sets the client default only; pass ``timeout=`` on an individual
    request when a call site needs to override it.
    """
    key = _loop_key(name)
    with _lock:
        existing = _clients.get(key)
        if existing is not None and not existing.is_closed:
            return existing
        client = httpx.AsyncClient(timeout=timeout, limits=limits or DEFAULT_LIMITS)
        _clients[key] = client
        return client


async def close_clients() -> None:
    """Close every shared client. Safe to call more than once."""
    with _lock:
        clients = list(_clients.items())
        _clients.clear()
    for key, client in clients:
        try:
            await client.aclose()
        except Exception as exc:
            logger.warning("http_client_close_failed", client=key[0], error=type(exc).__name__)
