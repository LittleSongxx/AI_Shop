"""MCP Streamable HTTP client used by the Agent runtime."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import structlog

from app.config.settings import get_settings

logger = structlog.get_logger()


class McpStreamableClient:
    """Calls Simlect MCP server over Streamable HTTP (tools/list + tools/call)."""

    def __init__(self, base_url: str | None = None):
        settings = get_settings()
        self._url = (base_url or settings.mcp_server_url).rstrip("/")

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Any]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        endpoint = self._url if self._url.endswith("/mcp") else f"{self._url}/mcp"
        async with streamablehttp_client(endpoint) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def list_tools(self) -> list[dict[str, Any]]:
        async with self._session() as session:
            result = await session.list_tools()
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

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        async with self._session() as session:
            result = await session.call_tool(name, arguments or {})
            parts: list[str] = []
            content = getattr(result, "content", None) or []
            for block in content:
                text = getattr(block, "text", None)
                if text:
                    parts.append(text)
                else:
                    parts.append(json.dumps(block.model_dump() if hasattr(block, "model_dump") else str(block)))
            if getattr(result, "isError", False):
                logger.warning("mcp_tool_error", tool=name, content=parts)
            return "\n".join(parts) if parts else ""


mcp_streamable_client = McpStreamableClient()
