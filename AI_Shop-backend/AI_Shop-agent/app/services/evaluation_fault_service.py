"""Local-evaluation-only fault capabilities for cross-process tests.

The evaluator cannot safely propagate a raw fault target through public request
fields.  Instead it registers a short-lived opaque capability in Redis.  The API
consumes that capability once, verifies its user/request/trial binding, and puts a
signed authorization descriptor on the durable task.  Worker and MCP processes
verify that descriptor before restoring a request-local failure scope.

Every entry point rejects production unconditionally.  The explicit local switch
is a second guard so ordinary development traffic cannot trigger faults either.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import hmac
import json
import re
import secrets
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.config.settings import get_settings
from app.services.redis_service import redis_service
from evaluation.core.fault_injection import FaultScenario, parse_fault_scenario

_SCHEMA_VERSION = "aishop-evaluation-fault-capability/v1"
_META_KEY = "aishopEvaluationFaultCapability"
_CAPABILITY_PREFIX = "mall:agent:evaluation:fault-capability:"
_EVENT_PREFIX = "mall:agent:evaluation:fault-events:"
_CLAIM_PREFIX = "mall:agent:evaluation:fault-claim:"
_DEFAULT_TTL_SECONDS = 600
_TOKEN_RE = re.compile(r"^efc_[A-Za-z0-9_-]{32,160}$")
_EVIDENCE_ID_RE = re.compile(r"^efe_[a-f0-9]{32}$")
_CROSS_PROCESS_TARGETS = frozenset(
    {"redis-checkpoint", "worker-deadline", "mcp-tool"}
)
_WORKER_TARGETS = frozenset({"redis-checkpoint", "worker-deadline"})


class FaultCapabilityRejected(RuntimeError):
    """An evaluation fault request was disabled, stale, forged, or mis-bound."""


@dataclass(frozen=True)
class RegisteredFaultCapability:
    token: str
    evidence_id: str


@dataclass(frozen=True)
class AuthorizedFault:
    evidence_id: str
    scenario: FaultScenario
    binding: dict[str, str]
    descriptor: dict[str, Any]


@dataclass(frozen=True)
class WorkerFaultActivation:
    authorized: AuthorizedFault | None = None
    mcp_capability: str | None = None

    @property
    def active(self) -> bool:
        return self.authorized is not None or self.mcp_capability is not None


def _guard_enabled() -> None:
    settings = get_settings()
    if settings.app_env.strip().casefold() == "production":
        raise FaultCapabilityRejected(
            "evaluation fault capabilities are forbidden in production"
        )
    if not settings.ai_eval_enable_fault_injection:
        raise FaultCapabilityRejected(
            "evaluation fault capabilities require AI_EVAL_ENABLE_FAULT_INJECTION=true"
        )


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _descriptor_signature(value: Mapping[str, Any]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "signature"}
    secret = get_settings().internal_token.encode("utf-8")
    return hmac.new(secret, _canonical_json(unsigned), hashlib.sha256).hexdigest()


def _capability_key(token: str) -> str:
    if not _TOKEN_RE.fullmatch(str(token or "")):
        raise FaultCapabilityRejected("malformed evaluation fault capability")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"{_CAPABILITY_PREFIX}{digest}"


def _event_key(evidence_id: str) -> str:
    if not _EVIDENCE_ID_RE.fullmatch(str(evidence_id or "")):
        raise FaultCapabilityRejected("malformed evaluation fault evidence ID")
    return f"{_EVENT_PREFIX}{evidence_id}"


def _claim_key(evidence_id: str) -> str:
    if not _EVIDENCE_ID_RE.fullmatch(str(evidence_id or "")):
        raise FaultCapabilityRejected("malformed evaluation fault evidence ID")
    return f"{_CLAIM_PREFIX}{evidence_id}"


def _required_binding(value: Mapping[str, Any]) -> dict[str, str]:
    binding = value.get("binding")
    if not isinstance(binding, Mapping):
        raise FaultCapabilityRejected("fault capability binding is missing")
    result = {
        "userId": str(binding.get("userId") or "").strip(),
        "requestId": str(binding.get("requestId") or "").strip(),
        "trialId": str(binding.get("trialId") or "").strip(),
    }
    if not all(result.values()):
        raise FaultCapabilityRejected("fault capability binding is incomplete")
    if any(len(item) > 160 for item in result.values()):
        raise FaultCapabilityRejected("fault capability binding is too long")
    run_id = str(binding.get("runId") or "").strip()
    if run_id:
        if len(run_id) > 160:
            raise FaultCapabilityRejected("fault capability run binding is too long")
        result["runId"] = run_id
    return result


def _parse_record(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    except (TypeError, json.JSONDecodeError) as exc:
        raise FaultCapabilityRejected("fault capability payload is malformed") from exc
    if not isinstance(value, dict) or value.get("schemaVersion") != _SCHEMA_VERSION:
        raise FaultCapabilityRejected("fault capability schema is invalid")
    expires_at = value.get("expiresAtEpoch")
    if not isinstance(expires_at, (int, float)) or expires_at < time.time():
        raise FaultCapabilityRejected("fault capability has expired")
    return value


def _verify_descriptor(value: Any) -> AuthorizedFault:
    if not isinstance(value, dict) or value.get("schemaVersion") != _SCHEMA_VERSION:
        raise FaultCapabilityRejected("authorized fault descriptor is malformed")
    signature = str(value.get("signature") or "")
    if not signature or not hmac.compare_digest(signature, _descriptor_signature(value)):
        raise FaultCapabilityRejected("authorized fault descriptor signature is invalid")
    expires_at = value.get("expiresAtEpoch")
    if not isinstance(expires_at, (int, float)) or expires_at < time.time():
        raise FaultCapabilityRejected("authorized fault descriptor has expired")
    evidence_id = str(value.get("evidenceId") or "")
    _event_key(evidence_id)
    binding = _required_binding(value)
    scenario = parse_fault_scenario(value.get("scenario"), index=1)
    if scenario.target not in _CROSS_PROCESS_TARGETS:
        raise FaultCapabilityRejected("fault target is not cross-process authorized")
    return AuthorizedFault(
        evidence_id=evidence_id,
        scenario=scenario,
        binding=binding,
        descriptor=dict(value),
    )


async def _record_event(evidence_id: str, event: Mapping[str, Any]) -> None:
    key = _event_key(evidence_id)
    payload = {
        **dict(event),
        "evidenceId": evidence_id,
        "observedAtEpochMs": int(time.time() * 1000),
    }
    await redis_service.client.rpush(
        key,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
    await redis_service.client.expire(key, _DEFAULT_TTL_SECONDS)


async def record_fault_events(
    authorized: AuthorizedFault,
    events: Sequence[Mapping[str, Any]],
    *,
    process: str,
) -> None:
    for event in events:
        await _record_event(
            authorized.evidence_id,
            {**dict(event), "process": process},
        )


async def _claim_injection(evidence_id: str, *, process: str) -> bool:
    claimed = await redis_service.client.set(
        _claim_key(evidence_id),
        process,
        nx=True,
        ex=_DEFAULT_TTL_SECONDS,
    )
    return bool(claimed)


async def register_fault_capability(
    scenario: FaultScenario,
    *,
    user_id: str,
    request_id: str,
    trial_id: str,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> RegisteredFaultCapability:
    """Register one evaluator-owned capability; never returns raw fault fields to API."""

    _guard_enabled()
    if scenario.target not in _CROSS_PROCESS_TARGETS:
        raise FaultCapabilityRejected(
            f"target {scenario.target!r} does not require a cross-process capability"
        )
    binding = _required_binding(
        {
            "binding": {
                "userId": user_id,
                "requestId": request_id,
                "trialId": trial_id,
            }
        }
    )
    ttl = max(30, min(int(ttl_seconds), _DEFAULT_TTL_SECONDS))
    for _attempt in range(4):
        token = "efc_" + secrets.token_urlsafe(36)
        evidence_id = "efe_" + secrets.token_hex(16)
        record = {
            "schemaVersion": _SCHEMA_VERSION,
            "kind": "ROOT",
            "evidenceId": evidence_id,
            "scenario": scenario.public(),
            "binding": binding,
            "expiresAtEpoch": time.time() + ttl,
        }
        created = await redis_service.client.set(
            _capability_key(token),
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            nx=True,
            ex=ttl,
        )
        if created:
            return RegisteredFaultCapability(token=token, evidence_id=evidence_id)
    raise RuntimeError("could not allocate a unique evaluation fault capability")


async def consume_api_fault_capability(
    token: str,
    *,
    user_id: str,
    request_id: str,
    trial_id: str,
) -> dict[str, Any]:
    """Consume a root capability and return a signed, non-secret task descriptor."""

    _guard_enabled()
    raw = await redis_service.client.getdel(_capability_key(token))
    if raw is None:
        raise FaultCapabilityRejected("evaluation fault capability is missing or consumed")
    record = _parse_record(raw)
    if record.get("kind") != "ROOT":
        raise FaultCapabilityRejected("evaluation fault capability kind is invalid")
    binding = _required_binding(record)
    actual = {
        "userId": str(user_id or "").strip(),
        "requestId": str(request_id or "").strip(),
        "trialId": str(trial_id or "").strip(),
    }
    if binding != actual:
        raise FaultCapabilityRejected("evaluation fault capability binding mismatch")
    scenario = parse_fault_scenario(record.get("scenario"), index=1)
    if scenario.target not in _CROSS_PROCESS_TARGETS:
        raise FaultCapabilityRejected("evaluation fault target is not authorized")
    descriptor: dict[str, Any] = {
        "schemaVersion": _SCHEMA_VERSION,
        "kind": "AUTHORIZED_TASK",
        "evidenceId": str(record.get("evidenceId") or ""),
        "scenario": scenario.public(),
        "binding": binding,
        "expiresAtEpoch": float(record["expiresAtEpoch"]),
    }
    descriptor["signature"] = _descriptor_signature(descriptor)
    await _record_event(
        descriptor["evidenceId"],
        {
            "eventType": "FAULT_CAPABILITY_CONSUMED",
            "scenarioId": scenario.scenario_id,
            "target": scenario.target,
            "mode": scenario.mode,
            "process": "agent-api",
        },
    )
    return descriptor


async def prepare_worker_fault(payload: Mapping[str, Any]) -> WorkerFaultActivation:
    """Verify a durable task descriptor and activate at most one injection attempt."""

    value = payload.get("evaluationFault")
    if value is None:
        return WorkerFaultActivation()
    _guard_enabled()
    authorized = _verify_descriptor(value)
    actual = {
        "userId": str(payload.get("userId") or "").strip(),
        "requestId": str(payload.get("requestId") or "").strip(),
        "trialId": str(payload.get("evaluationTrialId") or "").strip(),
    }
    if authorized.binding != actual:
        raise FaultCapabilityRejected("worker fault descriptor binding mismatch")

    # A retry of the same durable task must not re-inject the same fault.
    if await redis_service.client.exists(_claim_key(authorized.evidence_id)):
        return WorkerFaultActivation()
    if authorized.scenario.target in _WORKER_TARGETS:
        if not await _claim_injection(authorized.evidence_id, process="agent-worker"):
            return WorkerFaultActivation()
        return WorkerFaultActivation(authorized=authorized)

    if authorized.scenario.target != "mcp-tool":
        raise FaultCapabilityRejected("worker received an unsupported fault target")
    run_id = str(payload.get("runId") or "").strip()
    if not run_id:
        raise FaultCapabilityRejected("MCP fault task has no run binding")
    child_token = "efc_" + secrets.token_urlsafe(36)
    child_record = {
        "schemaVersion": _SCHEMA_VERSION,
        "kind": "MCP_CHILD",
        "evidenceId": authorized.evidence_id,
        "scenario": authorized.scenario.public(),
        "binding": {**authorized.binding, "runId": run_id},
        "descriptor": authorized.descriptor,
        "expiresAtEpoch": min(
            float(authorized.descriptor["expiresAtEpoch"]),
            time.time() + _DEFAULT_TTL_SECONDS,
        ),
    }
    created = await redis_service.client.set(
        _capability_key(child_token),
        json.dumps(child_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        nx=True,
        ex=_DEFAULT_TTL_SECONDS,
    )
    if not created:
        raise RuntimeError("could not allocate an MCP evaluation fault capability")
    return WorkerFaultActivation(
        authorized=authorized,
        mcp_capability=child_token,
    )


async def consume_mcp_fault_capability(
    token: str,
    *,
    arguments: Mapping[str, Any],
) -> AuthorizedFault:
    """Consume a Worker-issued child capability at the MCP dispatch boundary."""

    _guard_enabled()
    raw = await redis_service.client.getdel(_capability_key(token))
    if raw is None:
        raise FaultCapabilityRejected("MCP fault capability is missing or consumed")
    record = _parse_record(raw)
    if record.get("kind") != "MCP_CHILD":
        raise FaultCapabilityRejected("MCP fault capability kind is invalid")
    authorized = _verify_descriptor(record.get("descriptor"))
    binding = _required_binding(record)
    if any(
        binding.get(key) != authorized.binding.get(key)
        for key in ("userId", "requestId", "trialId")
    ):
        raise FaultCapabilityRejected("MCP child capability binding is inconsistent")
    actual_user = str(arguments.get("userId") or "").strip()
    actual_request = str(arguments.get("requestId") or "").strip()
    actual_run = str(arguments.get("runId") or "").strip()
    if actual_user != binding["userId"]:
        raise FaultCapabilityRejected("MCP fault capability user binding mismatch")
    if actual_request != binding["requestId"]:
        raise FaultCapabilityRejected("MCP fault capability request binding mismatch")
    if actual_run != binding.get("runId"):
        raise FaultCapabilityRejected("MCP fault capability run binding mismatch")
    if authorized.scenario.target != "mcp-tool":
        raise FaultCapabilityRejected("MCP capability target is invalid")
    if not await _claim_injection(authorized.evidence_id, process="mcp-server"):
        raise FaultCapabilityRejected("MCP fault was already injected")
    return AuthorizedFault(
        evidence_id=authorized.evidence_id,
        scenario=authorized.scenario,
        binding=binding,
        descriptor=authorized.descriptor,
    )


async def wait_for_fault_events(
    evidence_id: str,
    *,
    scenario_id: str,
    timeout_seconds: float = 5.0,
) -> list[dict[str, Any]]:
    """Wait briefly for a process-finally block to flush its boundary event."""

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    rows: list[dict[str, Any]] = []
    while True:
        raw_rows = await redis_service.client.lrange(_event_key(evidence_id), 0, -1)
        rows = []
        for raw in raw_rows:
            try:
                value = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                rows.append(value)
        if any(
            row.get("eventType") == "FAULT_INJECTED"
            and row.get("scenarioId") == scenario_id
            for row in rows
        ):
            return rows
        if time.monotonic() >= deadline:
            return rows
        await asyncio.sleep(0.1)


async def cleanup_fault_capability(
    registered: RegisteredFaultCapability,
) -> None:
    """Remove evaluator-owned evidence keys after they were copied into the run."""

    await redis_service.client.delete(
        _capability_key(registered.token),
        _event_key(registered.evidence_id),
        _claim_key(registered.evidence_id),
    )


@dataclass
class _McpFaultCapabilityState:
    token: str
    consumed: bool = False


_ACTIVE_MCP_CAPABILITY: contextvars.ContextVar[
    _McpFaultCapabilityState | None
] = contextvars.ContextVar(
    "aishop_evaluation_mcp_fault_capability",
    default=None,
)


class McpFaultCapabilityScope(AbstractContextManager["McpFaultCapabilityScope"]):
    def __init__(self, token: str):
        _capability_key(token)
        self._token_value = token
        self._context_token: contextvars.Token[_McpFaultCapabilityState | None] | None = None

    def __enter__(self) -> "McpFaultCapabilityScope":
        if _ACTIVE_MCP_CAPABILITY.get() is not None:
            raise RuntimeError("nested MCP fault capability scope is not allowed")
        # ContextVar values are copied when asyncio creates a child task. Keep the
        # one-shot bit in a shared mutable object so sibling tool-call tasks cannot
        # each replay the same child capability from their copied contexts.
        self._context_token = _ACTIVE_MCP_CAPABILITY.set(
            _McpFaultCapabilityState(token=self._token_value)
        )
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._context_token is not None:
            _ACTIVE_MCP_CAPABILITY.reset(self._context_token)
            self._context_token = None


def active_mcp_fault_meta() -> dict[str, str] | None:
    state = _ACTIVE_MCP_CAPABILITY.get()
    if state is None or state.consumed:
        return None
    # The child capability is one-shot. A model/tool retry in the same Worker
    # must execute normally instead of repeatedly presenting a consumed token.
    # No await occurs between the check and mutation, making this atomic within
    # the event loop while remaining visible to copied asyncio contexts.
    state.consumed = True
    return {_META_KEY: state.token}


def mcp_fault_capability_from_meta(meta: Any) -> str | None:
    if meta is None:
        return None
    value = getattr(meta, _META_KEY, None)
    if value is None and isinstance(getattr(meta, "model_extra", None), Mapping):
        value = meta.model_extra.get(_META_KEY)
    token = str(value or "").strip()
    return token or None
