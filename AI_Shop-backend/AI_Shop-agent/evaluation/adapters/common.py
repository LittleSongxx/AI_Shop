from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _auditable_not_applicable(snapshot: Mapping[str, Any]) -> bool:
    """Accept a provider short path only when it proves that no call was made."""

    if not bool(snapshot.get("notApplicable")):
        return False
    if not str(snapshot.get("notApplicableReason") or "").strip():
        return False
    request_count = max(
        int(snapshot.get("providerRequests") or 0),
        int(snapshot.get("requests") or 0),
        int(snapshot.get("providerCalls") or 0),
    )
    failure_count = max(
        int(snapshot.get("providerFailures") or 0),
        int(snapshot.get("failures") or 0),
        int(snapshot.get("breakerRejections") or 0),
    )
    return request_count == 0 and failure_count == 0


def provider_complete(
    required: Sequence[str],
    facts: Mapping[str, Mapping[str, Any]],
) -> tuple[int, dict[str, Any]]:
    decisions: dict[str, Any] = {}
    for provider in required:
        snapshot = dict(facts.get(provider) or {})
        not_applicable = _auditable_not_applicable(snapshot)
        if not_applicable:
            complete = True
        elif provider == "embedding":
            complete = (
                int(snapshot.get("providerRequests") or 0) > 0
                and int(snapshot.get("providerSuccesses") or 0)
                == int(snapshot.get("providerRequests") or 0)
                and int(snapshot.get("providerFailures") or 0) == 0
                and int(snapshot.get("breakerRejections") or 0) == 0
            )
        elif provider == "rerank":
            complete = (
                int(snapshot.get("eligibleRequests") or 0) > 0
                and int(snapshot.get("providerRequests") or 0) > 0
                and int(snapshot.get("providerSuccesses") or 0)
                == int(snapshot.get("providerRequests") or 0)
                and int(snapshot.get("providerFailures") or 0) == 0
                and int(snapshot.get("fallbackCount") or 0) == 0
            )
        elif provider == "llm":
            if "terminalSuccess" in snapshot:
                # A bounded transient retry is still a complete request when a
                # real terminal response was obtained. Failed attempts remain
                # visible in the provider and usage ledgers.
                complete = (
                    int(snapshot.get("requests") or 0) > 0
                    and int(snapshot.get("successes") or 0) > 0
                    and bool(snapshot.get("terminalSuccess"))
                )
            else:
                complete = (
                    int(snapshot.get("requests") or 0) > 0
                    and int(snapshot.get("successes") or 0)
                    == int(snapshot.get("requests") or 0)
                    and int(snapshot.get("failures") or 0) == 0
                )
        elif provider == "agent-runtime":
            complete = bool(snapshot.get("terminal")) and not snapshot.get("runtimeError")
        else:
            complete = False
        decisions[provider] = {
            "required": True,
            "complete": bool(complete),
            "notApplicable": bool(snapshot.get("notApplicable")),
            "notApplicableValid": not_applicable,
            "facts": snapshot,
        }
    return int(all(item["complete"] for item in decisions.values())), decisions


def assertion(name: str, passed: bool, detail: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}
