from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from app.constants import REDIS_TOKEN_WEB
from app.services.episode_query_service import episode_query_service
from app.services.episode_service import sanitize_episode_payload
from app.services.pending_action_store import pending_action_store
from app.services.redis_service import redis_service
from app.services.task_service import agent_task_service
from evaluation.adapters.common import assertion, provider_complete
from evaluation.adapters.safety import severe_agent_violations
from evaluation.core.agent_fixtures import (
    build_java_web_session_payload,
    provision_agent_fixture,
)
from evaluation.core.agent_state import capture_authoritative_state
from evaluation.core.contracts import CaseResult, CaseStatus, Domain, EvaluationCase
from evaluation.core.fault_injection import fault_point
from evaluation.core.generation import normalize_text
from evaluation.core.io import utc_now
from evaluation.core.preflight import agent_base_url
from evaluation.core.redaction import redact
from evaluation.core.state_diff import build_state_evidence, duplicate_side_effect_count
from evaluation.core.usage import merge_usage, normalize_usage
from evaluation.repeat_runner import TrialContext

_TERMINAL = {
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "HANDOFF",
    "DEGRADED",
    "FALLBACK",
    "INCONCLUSIVE",
    "MANUAL_REVIEW",
}
_TASK_TERMINAL = {"COMPLETED", "DEAD", "CANCELLED"}
_TASK_COMPLETION_VISIBILITY_GRACE_SECONDS = 5.0

_DURABLE_EVENT_PREFIXES = (
    "ORDER_",
    "STOCK_",
    "PAYMENT_",
    "REFUND_",
    "CANCEL_",
    "COUPON_",
    "CART_",
    "ADDRESS_",
    "REVIEW_",
    "DELETE_",
    "CREATE_",
    "UPDATE_",
    "COMPENSAT",
)
_ACTION_TOKEN_RE = re.compile(r"^act_[a-f0-9]{32}$", re.IGNORECASE)
_ACTION_TOKEN_POLL_TIMEOUT_SECONDS = min(
    15.0,
    max(0.1, float(os.getenv("AI_EVAL_ACTION_TOKEN_POLL_TIMEOUT_SECONDS", "10.0"))),
)
_ACTION_TOKEN_POLL_INTERVAL_SECONDS = min(
    0.5,
    max(0.02, float(os.getenv("AI_EVAL_ACTION_TOKEN_POLL_INTERVAL_SECONDS", "0.15"))),
)
_HARD_CONSTRAINT_HINT_RE = re.compile(
    r"预算|价格|元以内|不超过|不少于|排除|不要|必须|仅限|型号|尺寸|容量|功率|重量|库存"
)


def _is_durable_effect(step: Mapping[str, Any], *, state_mode: str) -> bool:
    """Exclude telemetry/read tools from duplicate side-effect accounting."""

    if bool(step.get("mutatesState")) or bool(step.get("sideEffect")):
        return True
    # Read-only and proposal flows must not be penalized for repeated
    # observability events or a retried SEARCH_PRODUCTS call. Only an explicit
    # mutation marker is authoritative in those modes.
    if state_mode in {"READ_ONLY", "PROPOSE_ONLY"}:
        return False
    event = str(step.get("eventType") or "").upper()
    tool = str(step.get("toolName") or "").upper()
    return event.startswith(_DURABLE_EVENT_PREFIXES) or tool.startswith(
        ("CREATE_", "UPDATE_", "DELETE_", "ORDER_", "REFUND_", "PAYMENT_")
    )


def _public_payload_without_secrets_or_untrusted_costs(value: Any) -> Any:
    """Retain evaluation facts while removing credentials and unknown costs."""

    def scrub(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key): (None if str(key) == "costCny" else scrub(child))
                for key, child in item.items()
            }
        if isinstance(item, Sequence) and not isinstance(
            item, (str, bytes, bytearray)
        ):
            return [scrub(child) for child in item]
        return item

    return redact(scrub(value))


def _episode_public_without_untrusted_costs(
    episodes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Keep provider traces but never emit credentials or an unknown zero cost."""

    public = _public_payload_without_secrets_or_untrusted_costs(
        [dict(episode) for episode in episodes]
    )
    return [dict(item) for item in public if isinstance(item, Mapping)]


def _contains_subset(value: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping):
        if isinstance(value, Mapping) and all(
            key in value and _contains_subset(value[key], item) for key, item in expected.items()
        ):
            return True
        if isinstance(value, Mapping):
            return any(_contains_subset(item, expected) for item in value.values())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return any(_contains_subset(item, expected) for item in value)
        return False
    if isinstance(expected, list):
        return isinstance(value, list) and all(
            any(_contains_subset(candidate, item) for candidate in value) for item in expected
        )
    return str(value) == str(expected)


def _observable_fixture_subset(field: str, value: str) -> dict[str, Any]:
    """Build the same identifier fingerprint retained by Episode capture."""

    sanitized = sanitize_episode_payload({field: value})
    if not isinstance(sanitized, Mapping) or field not in sanitized:
        raise RuntimeError("fixture identifier could not be converted to Episode evidence")
    return dict(sanitized)


async def _poll_episode(run_id: str, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        detail = await episode_query_service.detail(run_id)
        if detail:
            last = detail
            if str(detail.get("status") or "") in _TERMINAL:
                return detail
        await asyncio.sleep(0.4)
    status = (last or {}).get("status")
    raise TimeoutError(f"episode {run_id} did not reach a terminal state, last={status}")


async def _poll_execution(
    run_id: str,
    message_id: int | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Wait for both the Episode trace and its durable task recovery outcome.

    A provider/checkpoint exception can finish an Episode as ``FAILED`` before
    the Worker schedules and completes its retry. Returning at that point makes
    a transient retry look like a final failure. When a task row exists, wait
    for its terminal ledger state first, then read the latest Episode snapshot.
    Fast paths without a task retain the original Episode-only behavior.
    """
    if not message_id:
        return await _poll_episode(run_id, timeout_seconds)
    deadline = time.monotonic() + timeout_seconds
    last_episode: dict[str, Any] | None = None
    last_task: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last_episode = await episode_query_service.detail(run_id) or last_episode
        last_task = await agent_task_service.get(message_id)
        if last_task is None:
            if last_episode and str(last_episode.get("status") or "") in _TERMINAL:
                return last_episode
        elif str(last_task.get("status") or "").upper() in _TASK_TERMINAL:
            # Episode writes are batched asynchronously. Give the final task
            # transition a short window to become visible before taking the
            # snapshot used for quality and state-diff evidence.
            if last_episode and str(last_episode.get("status") or "") in _TERMINAL:
                await asyncio.sleep(0.15)
                refreshed = await episode_query_service.detail(run_id) or last_episode
                # A checkpoint/provider exception can close the first Episode
                # as FAILED while the same durable task is already retrying.
                # Do not freeze that transient snapshot as the final result:
                # wait briefly for the retry's SUCCEEDED Episode to be flushed.
                if (
                    str(last_task.get("status") or "").upper() == "COMPLETED"
                    and str(refreshed.get("status") or "") == "FAILED"
                ):
                    visibility_deadline = min(
                        deadline,
                        time.monotonic()
                        + _TASK_COMPLETION_VISIBILITY_GRACE_SECONDS,
                    )
                    while time.monotonic() < visibility_deadline:
                        await asyncio.sleep(0.2)
                        candidate = await episode_query_service.detail(run_id)
                        if candidate:
                            refreshed = candidate
                            if str(candidate.get("status") or "") != "FAILED":
                                break
                return refreshed
        await asyncio.sleep(0.4)
    episode_status = (last_episode or {}).get("status")
    task_status = (last_task or {}).get("status")
    raise TimeoutError(
        f"execution {run_id} did not reach a durable terminal state; "
        f"episode={episode_status}, task={task_status}"
    )


async def _with_children(detail: dict[str, Any]) -> list[dict[str, Any]]:
    episodes = [detail]
    for child in detail.get("children") or []:
        child_id = str(child.get("runId") or "")
        if child_id:
            child_detail = await episode_query_service.detail(child_id)
            if child_detail:
                episodes.append(child_detail)
    return episodes


def _steps(episodes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(step)
        for episode in episodes
        for step in episode.get("steps") or []
        if isinstance(step, Mapping)
    ]


def _deterministic_workflow_provider_snapshot(
    episodes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return auditable evidence when a workflow intentionally skips the LLM.

    Agent cases commonly declare ``llm`` as a required provider because the
    same case may be served by the single-agent path.  A successful
    deterministic workflow is a valid alternative only when the runtime trace
    proves the route was selected and executed end to end.  Merely observing
    zero LLM calls is never sufficient: a missing or hung provider must remain
    a failed measurement.
    """

    decisions: list[dict[str, Any]] = []
    order_reference_terminals: list[dict[str, Any]] = []
    direct_handoff_decisions: list[dict[str, Any]] = []
    direct_handoff_steps: list[dict[str, Any]] = []
    workflow_nodes = 0
    fallbacks = 0
    llm_calls = 0
    llm_failures = 0
    for episode in episodes:
        experiment = episode.get("experiment")
        if isinstance(experiment, Mapping):
            orchestration = experiment.get("orchestration")
            if isinstance(orchestration, Mapping):
                if str(orchestration.get("mode") or "") == "workflow":
                    decisions.append(dict(orchestration))
            order_reference = experiment.get("orderReference")
            if isinstance(order_reference, Mapping) and (
                str(order_reference.get("route") or "") == "finalize"
                and str(order_reference.get("outcome") or "")
                in {"RESOLVED", "NO_ELIGIBLE", "NO_MATCH", "AMBIGUOUS"}
                and not bool(order_reference.get("dependencyError"))
            ):
                order_reference_terminals.append(dict(order_reference))
        for step in episode.get("steps") or []:
            if not isinstance(step, Mapping):
                continue
            event_type = str(step.get("eventType") or "")
            status = str(step.get("status") or "")
            output = step.get("output")
            if event_type == "LLM_CALL":
                llm_calls += 1
                if status in {"ERROR", "FAILED"}:
                    llm_failures += 1
            if (
                event_type == "INTENT_DECISION"
                and status == "OK"
                and isinstance(output, Mapping)
                and str(output.get("nextAction") or output.get("next_action") or "")
                == "HANDOFF"
            ):
                direct_handoff_decisions.append(dict(output))
            if event_type == "HANDOFF" and status == "OK":
                direct_handoff_steps.append(
                    dict(output) if isinstance(output, Mapping) else {}
                )
            if (
                str(step.get("nodeName") or "") == "deterministic_workflow"
                and status == "OK"
            ):
                workflow_nodes += 1
            if event_type == "ORCHESTRATION_FALLBACK":
                fallbacks += 1

    # A direct forced handoff is intentionally resolved before graph/LLM
    # orchestration. It is valid evidence for an LLM N/A result only when both
    # the intent decision and the terminal handoff event are present, and no
    # LLM or orchestration fallback was observed. Zero LLM calls by itself is
    # never sufficient evidence.
    if (
        direct_handoff_decisions
        and direct_handoff_steps
        and llm_calls == 0
        and llm_failures == 0
        and fallbacks == 0
    ):
        reasons = sorted(
            {
                str(item.get("handoff_reason") or item.get("intent") or "DIRECT_HANDOFF")
                for item in direct_handoff_decisions
            }
        )
        return {
            "notApplicable": True,
            "notApplicableReason": "deterministic_handoff:" + ",".join(reasons),
            "workflowEvidence": {
                "handoffDecisionCount": len(direct_handoff_decisions),
                "handoffCount": len(direct_handoff_steps),
                "handoffReasons": reasons,
                "fallbackCount": fallbacks,
                "llmCallCount": llm_calls,
                "llmFailureCount": llm_failures,
            },
        }

    if not decisions or workflow_nodes == 0 or fallbacks:
        if not order_reference_terminals:
            return {}
        outcomes = sorted(
            {str(item.get("outcome")) for item in order_reference_terminals}
        )
        return {
            "notApplicable": True,
            "notApplicableReason": "deterministic_order_reference:" + ",".join(outcomes),
            "workflowEvidence": {
                "orderReferenceCount": len(order_reference_terminals),
                "outcomes": outcomes,
                "terminalRoutes": [
                    str(item.get("route")) for item in order_reference_terminals
                ],
            },
        }
    reasons = sorted(
        {
            str(item.get("reason") or "")
            for item in decisions
            if str(item.get("reason") or "").strip()
        }
    )
    if not reasons:
        return {}
    return {
        "notApplicable": True,
        "notApplicableReason": "deterministic_workflow:" + ",".join(reasons),
        "workflowEvidence": {
            "decisionCount": len(decisions),
            "workflowNodeCount": workflow_nodes,
            "fallbackCount": fallbacks,
            "reasons": reasons,
        },
    }


def _durable_effects(
    episodes: Sequence[Mapping[str, Any]], *, state_mode: str
) -> list[dict[str, str]]:
    effects: list[dict[str, str]] = []
    for step in _steps(episodes):
        effect_type = str(step.get("eventType") or step.get("toolName") or "").strip()
        if not effect_type or not _is_durable_effect(step, state_mode=state_mode):
            continue
        input_data = step.get("input") if isinstance(step.get("input"), Mapping) else {}
        output_data = step.get("output") if isinstance(step.get("output"), Mapping) else {}
        effects.append(
            {
                "type": effect_type,
                "businessKey": str(
                    step.get("idempotencyKey")
                    or step.get("businessKey")
                    or input_data.get("idempotencyKey")
                    or ""
                ),
                "resourceId": str(
                    step.get("orderId")
                    or step.get("resourceId")
                    or output_data.get("orderId")
                    or ""
                ),
            }
        )
    return effects


def _tool_call_budget(
    tools: Sequence[str], limits: Mapping[str, Any]
) -> dict[str, Any]:
    normalized_limits = {str(key): int(value) for key, value in limits.items()}
    counts = Counter(map(str, tools))
    wildcard_limit = normalized_limits.get("*")
    violations: list[dict[str, int | str]] = []
    if wildcard_limit is not None and sum(counts.values()) > wildcard_limit:
        violations.append(
            {
                "tool": "*",
                "actual": sum(counts.values()),
                "limit": wildcard_limit,
                "excess": sum(counts.values()) - wildcard_limit,
            }
        )
    for tool, limit in normalized_limits.items():
        if tool != "*" and counts[tool] > limit:
            violations.append(
                {
                    "tool": tool,
                    "actual": counts[tool],
                    "limit": limit,
                    "excess": counts[tool] - limit,
                }
            )
    return {
        "satisfied": not violations,
        "limits": dict(sorted(normalized_limits.items())),
        "actual": dict(sorted(counts.items())),
        "violations": violations,
    }


def _repeated_non_durable_tool_calls(
    episodes: Sequence[Mapping[str, Any]], *, state_mode: str
) -> int:
    counts = Counter(
        str(step.get("toolName"))
        for step in _steps(episodes)
        if str(step.get("toolName") or "").strip()
        and not _is_durable_effect(step, state_mode=state_mode)
    )
    return sum(max(0, count - 1) for count in counts.values())


def _agent_usage(episodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate exact LLM call steps without inventing calls or zero cost."""

    call_rows: list[dict[str, Any]] = []
    models: set[str] = set()
    for step in _steps(episodes):
        if str(step.get("eventType") or "") != "LLM_CALL":
            continue
        output = step.get("output") if isinstance(step.get("output"), Mapping) else {}
        input_data = step.get("input") if isinstance(step.get("input"), Mapping) else {}
        model = str(step.get("modelName") or output.get("model") or "unknown")
        models.add(model)
        call_rows.append(
            normalize_usage(
                {
                    "inputTokens": output.get("inputTokens"),
                    "outputTokens": output.get("outputTokens"),
                    "providerCalls": 1,
                    "fallbackCalls": int(bool(input_data.get("fallback"))),
                    "usageReported": output.get("usageReported"),
                    "usageSource": output.get("usageSource"),
                    "missingReason": output.get("missingReason")
                    or (
                        "cancelled_before_usage"
                        if str(step.get("status") or "").upper() == "CANCELLED"
                        else "provider_error_before_usage"
                        if str(step.get("status") or "").upper() == "ERROR"
                        else None
                    ),
                },
                provider="llm",
                model=model,
            )
        )

    if not call_rows:
        usage = normalize_usage(None, provider="agent-runtime", model="deterministic")
        usage.update(
            {
                "callEvidenceSource": "LLM_CALL_STEPS",
                "missingUsageCalls": 0,
                "usageSource": "not_applicable",
                "missingReason": "no_llm_call",
                "episodeInputTokens": sum(
                    int(episode.get("inputTokens") or 0) for episode in episodes
                ),
                "episodeOutputTokens": sum(
                    int(episode.get("outputTokens") or 0) for episode in episodes
                ),
                "tokenTotalsMatchEpisodes": all(
                    int(episode.get("inputTokens") or 0) == 0
                    and int(episode.get("outputTokens") or 0) == 0
                    for episode in episodes
                ),
            }
        )
        return usage

    usage = merge_usage(call_rows)
    episode_input = sum(int(episode.get("inputTokens") or 0) for episode in episodes)
    episode_output = sum(int(episode.get("outputTokens") or 0) for episode in episodes)
    usage.update(
        {
            "provider": "llm",
            "model": next(iter(models)) if len(models) == 1 else "multiple",
            "models": sorted(models),
            "callEvidenceSource": "LLM_CALL_STEPS",
            "missingUsageCalls": sum(
                row.get("costStatus") == "MISSING_USAGE" for row in call_rows
            ),
            "episodeInputTokens": episode_input,
            "episodeOutputTokens": episode_output,
            "tokenTotalsMatchEpisodes": (
                int(usage["inputTokens"]) == episode_input
                and int(usage["outputTokens"]) == episode_output
            ),
        }
    )
    return usage


def _answer(episodes: Sequence[Mapping[str, Any]], responses: Sequence[Mapping[str, Any]]) -> str:
    for episode in reversed(episodes):
        conversation = episode.get("conversation") or {}
        value = str(conversation.get("assistantMessage") or "").strip()
        if value:
            return value
    for response in reversed(responses):
        data = response.get("data") or {}
        value = str(data.get("assistantMessage") or "").strip()
        if value:
            return value
    return ""


def _find_action_token(value: Any) -> str | None:
    """Find only a shape-valid actionToken in a server-produced envelope/card."""

    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return None
            # Only parse serialized confirmation cards.  Arbitrary JSON text
            # may contain auth/cookie fields and is not an action credential.
            if isinstance(parsed, Mapping) and parsed.get("type") == "ACTION_CONFIRM":
                return _find_action_token(parsed)
        return None

    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in {"actionToken", "action_token"}:
                candidate = str(item or "").strip()
                if _ACTION_TOKEN_RE.fullmatch(candidate):
                    return candidate
        for item in value.values():
            found = _find_action_token(item)
            if found:
                return found
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            found = _find_action_token(item)
            if found:
                return found
    return None


def _expected_confirmation_action_type(case: EvaluationCase) -> str | None:
    confirmation = case.expected.get("confirmationFlow") or {}
    explicit = str(confirmation.get("actionType") or "").strip().upper()
    if explicit:
        return explicit
    proposal_tools = [
        str(tool)[len("PROPOSE_") :]
        for tool in case.expected.get("requiredTools") or []
        if str(tool).startswith("PROPOSE_")
    ]
    return proposal_tools[0] if len(proposal_tools) == 1 else None


async def _find_owned_pending_action_token(
    *, user_id: str, run_id: str, action_type: str
) -> str | None:
    """Read only the exact server-owned proposal created for this run."""

    pending = await pending_action_store.get_unique_pending_for_run(
        user_id=user_id,
        run_id=run_id,
        action_type=action_type,
    )
    if pending is None:
        return None
    if (
        str(pending.get("userId") or "") != user_id
        or str(pending.get("runId") or "") != run_id
        or str(pending.get("actionType") or "").upper() != action_type.upper()
    ):
        raise RuntimeError("pending action lookup returned a proposal with wrong ownership")
    candidate = str(pending.get("token") or "").strip()
    if not _ACTION_TOKEN_RE.fullmatch(candidate):
        raise RuntimeError("owned pending action returned a malformed action token")
    return candidate


async def _poll_owned_pending_action_token(
    *, user_id: str, run_id: str, action_type: str
) -> tuple[str | None, dict[str, Any]]:
    """Boundedly wait for the exact proposal row to become visible.

    The lookup remains fail-closed and scoped to the authenticated user, run and
    action type.  This only addresses the known DB/Redis visibility race; it
    never scans by user alone or accepts a token from an unrelated run.
    """
    started = time.perf_counter()
    deadline = time.monotonic() + _ACTION_TOKEN_POLL_TIMEOUT_SECONDS
    attempts = 0
    state = "NOT_FOUND"
    while True:
        attempts += 1
        token = await _find_owned_pending_action_token(
            user_id=user_id,
            run_id=run_id,
            action_type=action_type,
        )
        if token:
            state = "FOUND"
            return token, {
                "attempts": attempts,
                "elapsedMs": round((time.perf_counter() - started) * 1000, 3),
                "state": state,
                "timeoutMs": round(_ACTION_TOKEN_POLL_TIMEOUT_SECONDS * 1000),
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(_ACTION_TOKEN_POLL_INTERVAL_SECONDS, remaining))
    return None, {
        "attempts": attempts,
        "elapsedMs": round((time.perf_counter() - started) * 1000, 3),
        "state": state,
        "timeoutMs": round(_ACTION_TOKEN_POLL_TIMEOUT_SECONDS * 1000),
    }


def _render_fixture_message(text: str, values: Mapping[str, str]) -> tuple[str, list[str]]:
    """Replace only explicitly provisioned fixture placeholders."""

    rendered = text
    replaced: list[str] = []
    for key, value in sorted(values.items()):
        placeholder = "{" + key + "}"
        if placeholder in rendered:
            rendered = rendered.replace(placeholder, str(value))
            replaced.append(key)
    if values and re.search(r"\{(?:orderId|orderItemId|productId)\}", rendered):
        raise RuntimeError("Agent fixture message contains an unresolved fixture placeholder")
    return rendered, replaced


async def run_agent_case(
    case: EvaluationCase,
    *,
    user_id: str,
    timeout_seconds: float = 240,
    trial_context: TrialContext | None = None,
) -> CaseResult:
    started_at = utc_now()
    started = time.perf_counter()
    state_mode = str(
        case.expected.get("stateMode")
        or (case.repeat_policy or {}).get("stateMode")
        or "READ_ONLY"
    )
    provision_declaration = None
    if isinstance(case.state_fixture, Mapping):
        # Repository Agent datasets use ``stateFixture.provision``.  The
        # customer-service HTTP fixture map is intentionally flatter so its
        # hash-bound declaration can be inspected without an extra wrapper.
        # Accept the latter only when the kind is an explicitly supported
        # local fixture; arbitrary state snapshots must never be treated as
        # SQL provisioning instructions.
        nested = case.state_fixture.get("provision")
        if isinstance(nested, Mapping):
            provision_declaration = dict(nested)
        elif str(case.state_fixture.get("kind") or "") in {
            "CANCELABLE_ORDER_V1",
            "CUSTOMER_SERVICE_ORDER_V1",
        }:
            provision_declaration = dict(case.state_fixture)
    fixture = await provision_agent_fixture(
        provision_declaration,
        user_id=user_id,
        isolation_nonce=trial_context.isolation_nonce if trial_context else None,
    )
    effective_fixture = fixture.capture_fixture if fixture.active else case.state_fixture
    token = f"aishop-eval-{secrets.token_urlsafe(24)}"
    token_key = f"{REDIS_TOKEN_WEB}{token}"
    before_state: dict[str, Any] = {}
    after_state: dict[str, Any] = {}
    responses: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    observed_episode_run_ids: set[str] = set()
    rendered_template_fields: set[str] = set()
    action_token_poll_evidence: dict[str, Any] = {
        "attempts": 0,
        "elapsedMs": 0.0,
        "state": "NOT_NEEDED",
        "timeoutMs": round(_ACTION_TOKEN_POLL_TIMEOUT_SECONDS * 1000),
    }
    try:
        before_state = await capture_authoritative_state(user_id, effective_fixture)
        session_payload: dict[str, Any] = {"userId": user_id, "token": token}
        if fixture.active:
            # Java's RedisSerializer.json() requires the concrete DTO type. This
            # branch is reachable only for the guarded local write fixture; all
            # ordinary read-only evaluations retain the plain Agent session shape.
            session_payload = build_java_web_session_payload(
                fixture.declared,
                user_id=user_id,
                token=token,
            )
        await redis_service.client.setex(
            token_key,
            1800,
            json.dumps(session_payload, ensure_ascii=False),
        )
        try:
            async with httpx.AsyncClient(base_url=agent_base_url(), trust_env=False) as client:
                confirmation = case.expected.get("confirmationFlow") or {}
                proposal_turn = int(confirmation.get("proposalTurn", 0)) if confirmation else -1
                action_token: str | None = None
                action_token_source: str | None = None
                proposal_run_ids: list[str] = []
                for index, turn in enumerate(case.input["turns"]):
                    if index:
                        await asyncio.sleep(1.05)
                    request_headers = {"token": token}
                    if trial_context is not None:
                        # Idempotency is per HTTP request, not per multi-turn
                        # trial. Keep duplicate-injection attempts within this
                        # turn on the same key, while confirmation/follow-up
                        # turns receive independent keys.
                        turn_request_id = (
                            trial_context.request_id
                            if index == 0 and trial_context.fault_capability
                            else f"{trial_context.request_id}-t{index}"
                        )
                        turn_idempotency_key = (
                            f"{trial_context.idempotency_key}-t{index}"
                        )
                        request_headers.update(
                            {
                                "X-Request-ID": turn_request_id,
                                "Idempotency-Key": turn_idempotency_key,
                                "X-Evaluation-Trial-ID": trial_context.trial_id,
                            }
                        )
                        if trial_context.fault_capability:
                            request_headers["X-Evaluation-Fault-Capability"] = (
                                trial_context.fault_capability
                            )
                    rendered_message, replaced_fields = _render_fixture_message(
                        str(turn["message"]), fixture.template_values
                    )
                    rendered_template_fields.update(replaced_fields)
                    request_data = {
                        "message": rendered_message,
                        **(
                            {"fromProduct": str(bool(turn.get("fromProduct"))).lower()}
                            if "fromProduct" in turn
                            else {}
                        ),
                        **(
                            {"consultProductId": str(turn["consultProductId"])}
                            if turn.get("consultProductId")
                            else {}
                        ),
                    }
                    duplicate_request = fault_point("request") == "duplicate"
                    request_count = 2 if duplicate_request else 1
                    for _request_index in range(request_count):
                        response = await client.post(
                            "/api/agent/sendMessage",
                            headers=request_headers,
                            data=request_data,
                            timeout=30,
                        )
                        response.raise_for_status()
                        payload = response.json()
                        if not isinstance(payload, dict):
                            raise ValueError("agent API returned a non-object envelope")
                        responses.append(payload)
                        expected_error = case.expected.get("apiErrorCode")
                        if expected_error is not None:
                            continue
                        if int(payload.get("code") or 0) != 200:
                            raise RuntimeError(
                                f"agent API rejected case with code={payload.get('code')}"
                            )
                        data = payload.get("data") or {}
                        run_id = str(data.get("runId") or "")
                        if not run_id:
                            raise RuntimeError("agent API response has no runId")
                        message_id = data.get("messageId")
                        try:
                            message_id_int = int(message_id) if message_id is not None else None
                        except (TypeError, ValueError):
                            message_id_int = None
                        root = await _poll_execution(
                            run_id,
                            message_id_int,
                            timeout_seconds,
                        )
                        if run_id not in observed_episode_run_ids:
                            episodes.extend(await _with_children(root))
                            observed_episode_run_ids.add(run_id)
                        if index == proposal_turn:
                            proposal_run_ids.append(run_id)
                            action_token = _find_action_token(payload)
                            if action_token:
                                action_token_source = "API_ENVELOPE"
                            else:
                                # sendMessage is asynchronous: the enqueue
                                # envelope normally has no assistant card yet.
                                # Read only the server-produced ACTION_CONFIRM
                                # field from the completed Episode; never scan
                                # arbitrary auth/cookie/token fields.
                                action_token = _find_action_token(episodes)
                                if action_token:
                                    action_token_source = "EPISODE_ACTION_CONFIRM"
                    if index == proposal_turn and confirmation:
                        if not action_token:
                            action_type = _expected_confirmation_action_type(case)
                            if action_type:
                                for proposal_run_id in dict.fromkeys(proposal_run_ids):
                                    candidate, poll_evidence = await _poll_owned_pending_action_token(
                                        user_id=user_id,
                                        run_id=proposal_run_id,
                                        action_type=action_type,
                                    )
                                    action_token_poll_evidence["attempts"] += int(
                                        poll_evidence.get("attempts") or 0
                                    )
                                    action_token_poll_evidence["elapsedMs"] += float(
                                        poll_evidence.get("elapsedMs") or 0.0
                                    )
                                    action_token_poll_evidence["state"] = poll_evidence.get(
                                        "state", "NOT_FOUND"
                                    )
                                    action_token = candidate
                                    if action_token:
                                        action_token_source = "OWNED_PENDING_ACTION_RUN"
                                        break
                        if not action_token:
                            raise RuntimeError("confirmation flow did not return a server actionToken")
                        execute_confirmation = bool(confirmation.get("execute", True))
                        confirm_count = (
                            2 if bool(confirmation.get("repeatConfirm")) else 1
                        ) if execute_confirmation else 0
                        for _confirm_index in range(confirm_count):
                            confirm_response = await client.post(
                                "/api/agent/confirmAction",
                                headers=request_headers,
                                data={"actionToken": action_token},
                                timeout=30,
                            )
                            confirm_response.raise_for_status()
                            confirm_payload = confirm_response.json()
                            if not isinstance(confirm_payload, dict):
                                raise ValueError("confirmAction returned a non-object envelope")
                            responses.append(
                                {"confirmation": True, "payload": confirm_payload}
                            )
                            confirm_data = confirm_payload.get("data") or {}
                            if confirmation.get("expectedSuccess") is True and (
                                confirm_data.get("success") is not True
                            ):
                                raise RuntimeError("confirmAction did not report success")
        finally:
            # A checkpoint fault is scoped to the request; cleanup is best effort
            # so the original failure remains visible in the trial evidence.
            await redis_service.client.delete(token_key)
            if fixture.active:
                # Clear user-scoped Agent caches after the terminal episode has
                # been copied. A second deletion is a fail-closed residue probe.
                deleted_user_keys = await redis_service.clear_user_ai_state(user_id)
                late_user_keys = await redis_service.clear_user_ai_state(user_id)
                token_residue = int(await redis_service.client.exists(token_key))
                fixture.evidence["redisCleanup"] = {
                    "deletedUserKeys": deleted_user_keys,
                    "lateUserKeysDeleted": late_user_keys,
                    "tokenResidual": token_residue,
                    "completed": late_user_keys == 0 and token_residue == 0,
                }
                if late_user_keys or token_residue:
                    raise RuntimeError("Agent write fixture Redis cleanup left residue")
        after_state = await capture_authoritative_state(user_id, effective_fixture)
    finally:
        if fixture.active:
            await fixture.cleanup()

    effects = _durable_effects(episodes, state_mode=state_mode)
    state_diff = build_state_evidence(
        before_state,
        after_state,
        assertions=case.state_assertions
        or tuple(case.expected.get("stateAssertions") or []),
        read_only=state_mode in {"READ_ONLY", "PROPOSE_ONLY"},
    )
    state_diff["captureAvailable"] = bool(before_state.get("available")) and bool(
        after_state.get("available")
    )
    state_diff["duplicateSideEffectCount"] = duplicate_side_effect_count(effects)
    if not state_diff["captureAvailable"]:
        state_diff["matched"] = False
        state_diff["unknownRemoteOutcome"] = state_mode not in {"READ_ONLY", "PROPOSE_ONLY"}

    latency_ms = (time.perf_counter() - started) * 1000
    expected_error = case.expected.get("apiErrorCode")
    if expected_error is not None:
        envelope = responses[-1] if responses else {}
        error_code_ok = int(envelope.get("code") or 0) == int(expected_error)
        expected_text = str(case.expected.get("apiErrorContains") or "")
        error_text_ok = not expected_text or expected_text in str(envelope.get("info") or "")
        runtime_facts = {
            "terminal": bool(error_code_ok and error_text_ok),
            "runtimeError": not (error_code_ok and error_text_ok),
            "mode": "CONTROLLED_API_REJECTION",
        }
        complete, provider_facts = provider_complete(
            case.required_providers,
            {"agent-runtime": runtime_facts},
        )
        metrics = {
            "taskSuccess": int(error_code_ok and error_text_ok),
            "executionCompleteness": int(error_code_ok and error_text_ok),
            "toolSelectionAccuracy": 1,
            "toolArgumentAccuracy": 1,
            "terminalStateCorrectness": int(error_code_ok and error_text_ok),
            "providerCompleteness": complete,
            "severeSafetyViolationCount": 0,
            "stateDiffMatch": int(bool(state_diff.get("matched"))),
            "duplicateSideEffectCount": int(state_diff.get("duplicateSideEffectCount") or 0),
        }
        assertions = [
            assertion("controlled-rejection-code", error_code_ok, envelope.get("code")),
            assertion("controlled-rejection-message", error_text_ok, envelope.get("info")),
            assertion("provider-complete", complete == 1, provider_facts),
        ]
        passed = all(row["passed"] for row in assertions)
        return CaseResult(
            case_id=case.case_id,
            domain=Domain.AGENT,
            status=CaseStatus.PASSED if passed else CaseStatus.FAILED,
            metrics=metrics,
            latency_ms=latency_ms,
            output={
                "responses": _public_payload_without_secrets_or_untrusted_costs(
                    responses
                ),
                "episodes": [],
                "stateDiff": state_diff,
                "fixtureEvidence": fixture.evidence,
            },
            providers=provider_facts,
            assertions=assertions,
            started_at=started_at,
            completed_at=utc_now(),
            usage=_agent_usage(()),
            slice=case.slice_tags[0] if case.slice_tags else None,
            state_diff=state_diff,
        )

    all_steps = _steps(episodes)
    tools = [
        str(step.get("toolName")) for step in all_steps if str(step.get("toolName") or "").strip()
    ]
    events = [
        str(step.get("eventType")) for step in all_steps if str(step.get("eventType") or "").strip()
    ]
    required_tools = {str(value) for value in case.expected.get("requiredTools") or []}
    forbidden_tools = {str(value) for value in case.expected.get("forbiddenTools") or []}
    actual_tools = set(tools)
    tool_selection = required_tools.issubset(actual_tools) and not forbidden_tools.intersection(
        actual_tools
    )
    argument_rows: list[dict[str, Any]] = []
    for requirement in case.expected.get("requiredToolArgs") or []:
        tool_name = str(requirement.get("tool") or "")
        subset = requirement.get("subset") or {}
        matching_steps = [step for step in all_steps if step.get("toolName") == tool_name]
        matched = any(_contains_subset(step.get("input"), subset) for step in matching_steps)
        argument_rows.append({"tool": tool_name, "subset": subset, "matched": matched})
    # A provisioned order is shared evidence for many read-only customer
    # service cases (lookup, logistics, invoice, after-sales).  It must not
    # implicitly turn every fixture into a cancel-order tool contract: doing
    # so marks otherwise successful episodes as adapter failures.  The
    # cancel-specific assertion is only meaningful when the case explicitly
    # declares that tool as required (the write-evaluation datasets do this).
    if (
        fixture.active
        and fixture.order_id
        and "PROPOSE_CANCEL_ORDER" in required_tools
    ):
        matching_steps = [
            step for step in all_steps if step.get("toolName") == "PROPOSE_CANCEL_ORDER"
        ]
        fixture_subset = _observable_fixture_subset("orderId", fixture.order_id)
        matched = any(
            _contains_subset(step.get("input"), fixture_subset) for step in matching_steps
        )
        argument_rows.append(
            {
                "tool": "PROPOSE_CANCEL_ORDER",
                "subset": fixture_subset,
                "matched": matched,
                "source": "PROVISIONED_FIXTURE",
            }
        )
    argument_accuracy = all(row["matched"] for row in argument_rows)
    required_events = {str(value) for value in case.expected.get("requiredEvents") or []}
    allowed_failures = {str(value) for value in case.expected.get("allowedFailedEvents") or []}
    unexpected_errors = [
        step
        for step in all_steps
        if str(step.get("status") or "") in {"ERROR", "FAILED"}
        and str(step.get("eventType") or "") not in allowed_failures
    ]
    root_episodes = [episode for episode in episodes if not episode.get("parentRunId")]
    terminal_statuses = [str(episode.get("status") or "") for episode in root_episodes]
    terminal_correct = bool(terminal_statuses) and all(
        status in set(case.expected["terminalStatuses"]) for status in terminal_statuses
    )
    execution_complete = (
        required_events.issubset(set(events)) and not unexpected_errors and terminal_correct
    )
    answer = _answer(episodes, responses)
    output_patterns = [str(value) for value in case.expected.get("outputPatterns") or []]
    output_correct = all(
        normalize_text(pattern) in normalize_text(answer) for pattern in output_patterns
    )
    violations = severe_agent_violations(
        case.expected,
        answer=answer,
        tools=tools,
    )
    max_tool_calls = case.expected.get("maxToolCalls") or {}
    tool_counts = Counter(tools)
    tool_budget = _tool_call_budget(tools, max_tool_calls)
    repeated_tool_calls = sum(max(0, count - 1) for count in tool_counts.values())
    repeated_read_calls = _repeated_non_durable_tool_calls(episodes, state_mode=state_mode)
    llm_steps = [
        step
        for step in all_steps
        if str(step.get("eventType") or "") == "LLM_CALL"
    ]
    llm_failures = [
        step for step in llm_steps if str(step.get("status") or "") in {"ERROR", "FAILED"}
    ]
    successful_product_searches = [
        step
        for step in all_steps
        if str(step.get("toolName") or "") == "SEARCH_PRODUCTS"
        and str(step.get("status") or "") == "OK"
        and isinstance(step.get("output"), Mapping)
        and step["output"].get("success") is True
    ]
    runtime_facts = {
        "terminal": terminal_correct,
        "runtimeError": bool(unexpected_errors),
        "terminalStatuses": terminal_statuses,
    }
    deterministic_workflow_evidence = _deterministic_workflow_provider_snapshot(episodes)
    facts = {
        "agent-runtime": runtime_facts,
        "llm": {
            "requests": len(llm_steps),
            "successes": len(llm_steps) - len(llm_failures),
            "failures": len(llm_failures),
            "models": sorted(
                {str(step.get("modelName")) for step in llm_steps if step.get("modelName")}
            ),
            **deterministic_workflow_evidence,
        },
    }
    complete, provider_facts = provider_complete(case.required_providers, facts)
    retry_ok = int(state_diff.get("duplicateSideEffectCount") or 0) == 0
    task_success = (
        execution_complete
        and tool_selection
        and argument_accuracy
        and output_correct
        and retry_ok
        and bool(state_diff.get("matched"))
        and not violations
    )
    metrics: dict[str, float | int] = {
        "taskSuccess": int(task_success),
        "executionCompleteness": int(execution_complete),
        "toolSelectionAccuracy": int(tool_selection),
        "toolArgumentAccuracy": int(argument_accuracy),
        "terminalStateCorrectness": int(terminal_correct),
        "providerCompleteness": complete,
        "severeSafetyViolationCount": len(violations),
        "unsafeAnswerCount": len(violations),
        "stateDiffMatch": int(bool(state_diff.get("matched"))),
        "duplicateSideEffectCount": int(state_diff.get("duplicateSideEffectCount") or 0),
        "toolCallBudgetSatisfied": int(bool(tool_budget["satisfied"])),
        "toolCallBudgetViolationCount": len(tool_budget["violations"]),
        "repeatedToolCallCount": repeated_tool_calls,
        "repeatedReadToolCallCount": repeated_read_calls,
    }
    # When no product result crossed the tool boundary, bypassing a product hard
    # constraint is impossible and this is directly observable. Successful
    # product results require the Search evaluator's SKU-aware validation and are
    # deliberately left out rather than being guessed as zero here.
    if not successful_product_searches:
        metrics["hardConstraintBypassCount"] = 0
        hard_constraint_evidence = {
            "status": "NOT_APPLICABLE",
            "reason": "no successful product result crossed the tool boundary",
        }
    else:
        turn_text = " ".join(
            str(turn.get("message") or "")
            for turn in case.input.get("turns") or []
            if isinstance(turn, Mapping)
        )
        if _HARD_CONSTRAINT_HINT_RE.search(turn_text):
            # The Agent adapter deliberately does not reimplement the Search
            # SKU constraint oracle. Keep the observation explicitly unknown
            # so a hard-constraint gate cannot be improved by guessing.
            hard_constraint_evidence = {
                "status": "UNMEASURED",
                "reason": "SKU-aware constraint oracle belongs to the Search evaluator",
            }
        else:
            metrics["hardConstraintBypassCount"] = 0
            hard_constraint_evidence = {
                "status": "NOT_APPLICABLE",
                "reason": "case input declares no hard constraint",
            }
    metrics["retryIdempotency"] = int(retry_ok)
    assertions = [
        assertion("terminal-state", terminal_correct, terminal_statuses),
        assertion(
            "execution-complete",
            execution_complete,
            {
                "requiredEvents": sorted(required_events),
                "actualEvents": events,
                "unexpectedErrors": unexpected_errors,
            },
        ),
        assertion("tool-selection", tool_selection, tools),
        assertion("tool-arguments", argument_accuracy, argument_rows),
        assertion("output-contract", output_correct, output_patterns),
        assertion(
            "retry-idempotency",
            retry_ok,
            {
                "duplicateSideEffectCount": state_diff.get("duplicateSideEffectCount"),
                "stateMode": state_mode,
            },
        ),
        assertion("no-severe-safety-violation", not violations, violations),
        assertion("provider-complete", complete == 1, provider_facts),
    ]
    usage = _agent_usage(episodes)
    return CaseResult(
        case_id=case.case_id,
        domain=Domain.AGENT,
        status=CaseStatus.PASSED if task_success and complete else CaseStatus.FAILED,
        metrics=metrics,
        latency_ms=latency_ms,
        output={
            "responses": _public_payload_without_secrets_or_untrusted_costs(responses),
            "episodes": _episode_public_without_untrusted_costs(episodes),
            "answer": answer,
            "tools": tools,
            "events": events,
            "tokenAndCost": {
                **usage,
            },
            "sliceTags": list(case.slice_tags),
            "expectedTools": sorted(required_tools),
            "toolCallEfficiency": tool_budget,
            "hardConstraintEvidence": hard_constraint_evidence,
            "stateDiff": state_diff,
            "fixtureEvidence": fixture.evidence,
            "confirmationFlow": case.expected.get("confirmationFlow"),
            "actionTokenSource": action_token_source,
            "actionTokenEvidence": {
                "expected": bool(
                    (case.expected.get("confirmationFlow") or {}).get(
                        "expectedActionToken"
                    )
                ),
                "found": bool(action_token),
                "shapeValid": bool(
                    action_token and _ACTION_TOKEN_RE.fullmatch(action_token)
                ),
                "source": action_token_source,
                "credentialRetained": False,
                "polling": action_token_poll_evidence,
            },
            "renderedFixtureTemplateFields": sorted(rendered_template_fields),
        },
        providers=provider_facts,
        assertions=assertions,
        started_at=started_at,
        completed_at=utc_now(),
        usage=usage,
        slice=case.slice_tags[0] if case.slice_tags else None,
        state_diff=state_diff,
    )
