"""MCP Streamable HTTP client used by the Agent runtime.

One initialized session is held open for the process instead of being rebuilt per
call. Rebuilding cost four HTTP round trips (connect, ``initialize``,
``notifications/initialized``, the call itself, then a DELETE to terminate) plus a
TCP/TLS handshake, for every tool the model invoked in every ReAct round.

The SDK's transport and ``ClientSession`` are task-scoped async context managers -
anyio requires the task that entered a cancel scope to be the one that exits it -
so the session cannot simply be cached and reused from arbitrary callers. Instead a
dedicated task holds it open and hands the session out. Callers only touch the
memory object streams underneath, which are safe to use from other tasks and which
multiplex concurrent requests by request id.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

import httpx
import structlog

from app.config.settings import get_settings
from app.domain.tool_policy import build_tool_manifest
from app.services.evaluation_fault_service import active_mcp_fault_meta
from app.services.mcp_trusted_context import build_trusted_turn_meta
from app.services.tool_invoke_result import (
    MCP_PROTOCOL,
    MCP_TOOL_CONTRACT,
    ToolInvokeResult,
    parse_tool_wire,
)

logger = structlog.get_logger()

# CONNECTION_CLOSED, and the code the transport reports when the server no longer
# recognises our session id (it answers 404, e.g. after a restart). Both mean the
# session is gone and a new one has to be initialized. The SDK currently emits
# "session terminated" as a positive 32600 where JSON-RPC codes are negative, so
# both signs are accepted rather than depending on that.
_CONNECTION_CLOSED = -32000
_SESSION_LOST_CODES = frozenset({_CONNECTION_CLOSED, 32600, -32600})

# The server streams tool results over SSE, so reads outlive a normal request.
_SSE_READ_TIMEOUT = 300.0
_STOP_TIMEOUT = 5.0


class _Connection:
    """State for one attempt at holding a session open.

    Each attempt gets its own object so a task that is shutting down can never
    write its outcome over the state of the session that replaced it.
    """

    def __init__(self) -> None:
        self.ready = asyncio.Event()
        self.stop = asyncio.Event()
        self.session: Any | None = None
        self.error: BaseException | None = None
        self.task: asyncio.Task | None = None


def _log_session_exit(task: asyncio.Task) -> None:
    """Retrieve the task's exception so it is never left unhandled.

    ``_serve`` records ordinary failures itself, but anyio task groups can raise a
    ``BaseExceptionGroup``, which its ``except Exception`` deliberately does not
    swallow.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("mcp_session_exited", error_type=type(exc).__name__, error=str(exc))


class _SessionHolder:
    """Keeps one live MCP session, rebuilding it when it dies."""

    def __init__(self, endpoint: str, headers: dict[str, str], timeout: float) -> None:
        self._endpoint = endpoint
        self._headers = headers
        self._timeout = timeout
        self._conn: _Connection | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def get(self) -> Any:
        loop = asyncio.get_running_loop()
        conn = self._conn
        stale_loop = self._loop is not loop
        finished = conn is not None and conn.task is not None and conn.task.done()
        if conn is None or stale_loop or finished:
            # No lock needed: nothing below awaits before _conn is reassigned, so
            # two callers cannot both spawn. A different loop means whatever is
            # cached belongs to a loop that is gone (tests, or a restarted worker).
            conn = self._spawn(loop)
        await conn.ready.wait()
        session = conn.session
        if session is None:
            raise conn.error or RuntimeError("MCP 会话不可用")
        return session

    def _spawn(self, loop: asyncio.AbstractEventLoop) -> _Connection:
        conn = _Connection()
        self._conn = conn
        self._loop = loop
        conn.task = loop.create_task(self._serve(conn))
        conn.task.add_done_callback(_log_session_exit)
        return conn

    async def _serve(self, conn: _Connection) -> None:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        try:
            # The client's lifetime is the session's lifetime, so this task owns
            # it rather than borrowing one from the shared registry.
            async with httpx.AsyncClient(
                headers=self._headers,
                timeout=httpx.Timeout(self._timeout, read=_SSE_READ_TIMEOUT),
                follow_redirects=True,
            ) as client:
                async with streamable_http_client(
                    self._endpoint, http_client=client
                ) as (read, write, _get_session_id):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        conn.session = session
                        conn.ready.set()
                        await conn.stop.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            conn.error = exc
            logger.warning(
                "mcp_session_failed", error=str(exc), error_type=type(exc).__name__
            )
        finally:
            conn.session = None
            # Unblock anyone waiting on a session that will never arrive; they
            # raise conn.error instead of hanging.
            conn.ready.set()

    async def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None or conn.task is None:
            return
        conn.stop.set()
        # asyncio.wait never re-raises the task's exception, which keeps a failed
        # session from turning shutdown into an error path.
        done, _pending = await asyncio.wait({conn.task}, timeout=_STOP_TIMEOUT)
        if not done:
            logger.warning("mcp_session_close_timeout")
            conn.task.cancel()
            await asyncio.wait({conn.task}, timeout=_STOP_TIMEOUT)


def _session_lost(exc: Any) -> bool:
    code = getattr(getattr(exc, "error", None), "code", None)
    return code in _SESSION_LOST_CODES


class McpStreamableClient:
    """Calls AI_Shop MCP server over Streamable HTTP (tools/list + tools/call)."""

    def __init__(self, base_url: str | None = None):
        settings = get_settings()
        self._url = (base_url or settings.mcp_server_url).rstrip("/")
        self._headers = {"X-Internal-Token": settings.internal_token}
        self._timeout = settings.mcp_timeout
        endpoint = self._url if self._url.endswith("/mcp") else f"{self._url}/mcp"
        self._holder = _SessionHolder(endpoint, self._headers, self._timeout)

    async def _with_session(
        self, action: Callable[[Any], Awaitable[Any]], *, what: str
    ) -> Any:
        """Run ``action`` against the live session, rebuilding it once if it died.

        A server restart invalidates our session id, and the first call after it
        is the one that finds out. Reconnecting here keeps that failure from
        surfacing as a tool error to the model.
        """
        from mcp.shared.exceptions import McpError

        for attempt in (1, 2):
            session = await self._holder.get()
            try:
                return await action(session)
            except McpError as exc:
                if attempt == 2 or not _session_lost(exc):
                    raise
                logger.info("mcp_session_rebuild", what=what, reason=str(exc))
                await self._holder.close()
        raise RuntimeError("MCP 会话重建失败")

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._with_session(
            lambda session: session.list_tools(), what="list_tools"
        )
        tools = getattr(result, "tools", None) or []
        out = []
        for t in tools:
            out.append(
                {
                    "name": getattr(t, "name", None),
                    "description": getattr(t, "description", None),
                }
            )
        return out

    async def tool_manifest(self) -> dict[str, Any]:
        """Report the live MCP registry against the local governance table."""

        try:
            listed = await self.list_tools()
        except Exception as exc:
            manifest = build_tool_manifest(
                timeout_seconds=self._timeout,
                registry_health="UNAVAILABLE",
            )
            manifest["reason"] = type(exc).__name__
            return manifest
        return build_tool_manifest(
            timeout_seconds=self._timeout,
            listed_tools=(tool.get("name") for tool in listed if tool.get("name")),
            registry_health="READY",
        )

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> ToolInvokeResult:
        args = arguments or {}
        evaluation_meta = active_mcp_fault_meta()
        trusted_turn_meta = build_trusted_turn_meta(name, args)
        request_meta = {
            **(evaluation_meta or {}),
            **(trusted_turn_meta or {}),
        }
        if trusted_turn_meta:
            trusted_payload = next(iter(trusted_turn_meta.values()))
            logger.info(
                "mcp_trusted_turn_context_attached",
                tool=name,
                user_text_chars=len(str(trusted_payload.get("userText") or "")),
                has_request_binding=bool(trusted_payload.get("requestId")),
            )
        raw = await self._with_session(
            (
                (lambda session: session.call_tool(name, args, meta=request_meta))
                if request_meta
                else (lambda session: session.call_tool(name, args))
            ),
            what=name,
        )
        parts: list[str] = []
        content = getattr(raw, "content", None) or []
        for block in content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
            else:
                parts.append(json.dumps(block.model_dump() if hasattr(block, "model_dump") else str(block)))
        if getattr(raw, "isError", False):
            logger.warning("mcp_tool_error", tool=name, content=parts)
            raise RuntimeError("MCP 工具返回错误结果")
        result = parse_tool_wire("\n".join(parts) if parts else "")
        if result.protocol_version != MCP_PROTOCOL:
            raise RuntimeError("MCP 协议契约不匹配")
        if result.contract_version != MCP_TOOL_CONTRACT:
            raise RuntimeError("MCP 工具契约不匹配")
        return result

    async def check_contract(self) -> bool:
        result = await self.call_tool("MCP_CONTRACT", {})
        return result.content == "ok"

    async def runtime_identity(self) -> dict[str, Any]:
        """Read the MCP process's safe startup identity through its contract.

        This is deliberately a dedicated system tool instead of an HTTP health
        endpoint so the same internal-token authentication and MCP session are
        exercised as real tool traffic.  A legacy MCP server cannot silently
        satisfy this check because it does not expose this tool.
        """

        result = await self.call_tool("MCP_RUNTIME_IDENTITY", {})
        try:
            value = json.loads(result.content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("MCP runtime identity is not valid JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError("MCP runtime identity must be an object")
        return value

    async def close(self) -> None:
        await self._holder.close()


mcp_streamable_client = McpStreamableClient()
