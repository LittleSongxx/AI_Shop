"""Hidden, request-bound context for MCP tools that need the original turn.

The model controls ordinary tool arguments such as ``keyword``. Search hard
constraints must instead come from the user message accepted by the Worker.
This module carries that text in MCP ``_meta`` and validates its request binding
without adding a model-visible tool parameter.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.episode_service import current_episode

TRUSTED_TURN_META_KEY = "aishopTrustedTurnContext"
TRUSTED_TURN_SCHEMA = "aishop-trusted-turn-context/v1"
_SUPPORTED_TOOLS = frozenset({"SEARCH_PRODUCTS"})
_MAX_USER_TEXT_CHARS = 4000


class TrustedTurnContextRejected(ValueError):
    """The caller supplied trusted metadata that failed its binding checks."""


@dataclass(frozen=True)
class TrustedTurnContext:
    tool_name: str
    user_id: str
    run_id: str
    request_id: str | None
    user_text: str


def build_trusted_turn_meta(
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build metadata only from the Worker's current Episode context."""

    normalized_tool = str(tool_name or "").strip().upper()
    if normalized_tool not in _SUPPORTED_TOOLS:
        return None
    context = current_episode()
    if context is None or not context.trusted_user_text:
        return None
    args = arguments or {}
    argument_user_id = str(args.get("userId") or args.get("user_id") or "").strip()
    if argument_user_id and argument_user_id != context.user_id:
        raise TrustedTurnContextRejected(
            "trusted Episode user does not match the MCP call user"
        )
    payload: dict[str, Any] = {
        "schemaVersion": TRUSTED_TURN_SCHEMA,
        "toolName": normalized_tool,
        "userId": context.user_id,
        "runId": context.run_id,
        "requestId": context.request_id or f"req_{context.run_id}",
        "userText": context.trusted_user_text[:_MAX_USER_TEXT_CHARS],
    }
    return {TRUSTED_TURN_META_KEY: payload}


def _meta_extra(meta: Any, key: str) -> Any:
    if meta is None:
        return None
    if isinstance(meta, Mapping):
        return meta.get(key)
    value = getattr(meta, key, None)
    if value is not None:
        return value
    model_extra = getattr(meta, "model_extra", None)
    return model_extra.get(key) if isinstance(model_extra, Mapping) else None


def trusted_turn_context_from_meta(
    meta: Any,
    *,
    tool_name: str,
    arguments: Mapping[str, Any] | None,
) -> TrustedTurnContext | None:
    """Validate hidden metadata against the actual MCP tool call."""

    raw = _meta_extra(meta, TRUSTED_TURN_META_KEY)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TrustedTurnContextRejected("trusted turn metadata must be an object")

    normalized_tool = str(tool_name or "").strip().upper()
    if normalized_tool not in _SUPPORTED_TOOLS:
        raise TrustedTurnContextRejected("trusted turn metadata is not allowed for this tool")
    if str(raw.get("schemaVersion") or "") != TRUSTED_TURN_SCHEMA:
        raise TrustedTurnContextRejected("trusted turn metadata schema mismatch")
    if str(raw.get("toolName") or "").strip().upper() != normalized_tool:
        raise TrustedTurnContextRejected("trusted turn metadata tool mismatch")

    args = arguments or {}
    actual_user_id = str(args.get("userId") or args.get("user_id") or "").strip()
    bound_user_id = str(raw.get("userId") or "").strip()
    if not actual_user_id or bound_user_id != actual_user_id:
        raise TrustedTurnContextRejected("trusted turn metadata user binding mismatch")

    bound_run_id = str(raw.get("runId") or "").strip()
    actual_run_id = str(args.get("runId") or args.get("run_id") or "").strip()
    if not bound_run_id or not actual_run_id or bound_run_id != actual_run_id:
        raise TrustedTurnContextRejected("trusted turn metadata run binding mismatch")

    bound_request_id = str(raw.get("requestId") or "").strip() or None
    actual_request_id = str(
        args.get("requestId") or args.get("request_id") or ""
    ).strip()
    if not bound_request_id or not actual_request_id or bound_request_id != actual_request_id:
        raise TrustedTurnContextRejected("trusted turn metadata request binding mismatch")

    user_text = str(raw.get("userText") or "").strip()
    if not user_text or len(user_text) > _MAX_USER_TEXT_CHARS:
        raise TrustedTurnContextRejected("trusted turn metadata user text is invalid")
    return TrustedTurnContext(
        tool_name=normalized_tool,
        user_id=bound_user_id,
        run_id=bound_run_id,
        request_id=bound_request_id,
        user_text=user_text,
    )
