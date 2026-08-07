from __future__ import annotations

import json
from typing import Any

_TERMINAL_RUN_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "HANDOFF", "DEGRADED"})
_KNOWN_ACTION_OUTCOMES = frozenset({"CONFIRMED", "FAILED"})


def evaluate_order_aftersales_episode(episode: dict[str, Any]) -> dict[str, Any]:
    """Evaluate training-data readiness from persisted, factual Episode fields.

    This deliberately does not calculate a scalar reward. It identifies whether
    the trajectory has enough deterministic evidence to enter human review and
    whether that review has explicitly approved it for downstream dataset use.
    """

    quality = _object(episode.get("quality") or episode.get("quality_json"))
    signals = _object(episode.get("rewardSignals") or episode.get("reward_signals_json"))
    verifier = _object(signals.get("verifier"))

    scenario = str(episode.get("scenario") or "").upper()
    action_type = str(signals.get("actionType") or "").upper()
    aftersales = scenario == "ORDER_AFTERSALES" and bool(
        action_type or signals.get("caseCreated") or signals.get("humanResolved")
    )
    verifier_passed = _strict_bool(
        verifier.get("passed"),
        default=_strict_bool(quality.get("verifierPassed"), default=False),
    )
    run_status = str(episode.get("status") or "").upper()
    run_terminal = run_status in _TERMINAL_RUN_STATUSES

    action_proposed = signals.get("actionProposed") is True
    user_confirmed = signals.get("userConfirmed") is True
    remote_outcome_known = signals.get("remoteOutcomeKnown") is True
    action_outcome = str(signals.get("outcome") or "").upper()

    case_created = signals.get("caseCreated") is True
    case_status = str(signals.get("caseStatus") or "").upper()
    human_resolved = signals.get("humanResolved") is True
    support_resolution_complete = bool(
        human_resolved
        and str(signals.get("humanResolutionCode") or "").strip()
        and str(signals.get("humanRootCause") or "").strip()
        and isinstance(signals.get("humanResolutionSummaryFingerprint"), dict)
        and signals["humanResolutionSummaryFingerprint"].get("sha256")
    )

    action_terminal = bool(
        action_proposed
        and user_confirmed
        and remote_outcome_known
        and action_outcome in _KNOWN_ACTION_OUTCOMES
    )
    if action_type == "CREATE_SUPPORT_CASE" or case_created:
        fact_complete = support_resolution_complete
    else:
        fact_complete = action_terminal

    verdict = "COMPLETE"
    if not aftersales:
        verdict = "NOT_ORDER_AFTERSALES"
    elif not verifier_passed:
        verdict = "VERIFIER_FAILED"
    elif not run_terminal:
        verdict = "RUN_NOT_TERMINAL"
    elif action_type == "CREATE_SUPPORT_CASE" or case_created:
        if support_resolution_complete:
            verdict = "SUPPORT_CASE_RESOLVED"
        elif case_status in {"OPEN", "IN_PROGRESS", ""}:
            verdict = "SUPPORT_CASE_OPEN"
        else:
            verdict = "SUPPORT_CASE_WITHOUT_RESOLUTION"
    elif action_proposed and not user_confirmed:
        verdict = "AWAITING_CONFIRMATION"
    elif user_confirmed and not remote_outcome_known:
        verdict = "OUTCOME_UNKNOWN"
    elif action_terminal and action_type == "CANCEL_ORDER" and action_outcome == "CONFIRMED":
        verdict = "CANCEL_CONFIRMED"
    elif action_terminal and action_outcome == "FAILED":
        verdict = "ACTION_FAILED_WITH_KNOWN_OUTCOME"
    elif not action_terminal:
        verdict = "INCOMPLETE_ACTION_FACTS"

    review_eligible = bool(aftersales and verifier_passed and run_terminal and fact_complete)
    review_status = str(
        episode.get("datasetEligible") or episode.get("dataset_eligible") or "UNREVIEWED"
    ).upper()
    training_eligible = review_eligible and review_status == "APPROVED"

    return {
        "schemaVersion": 1,
        "verdict": verdict,
        "orderAftersales": aftersales,
        "runTerminal": run_terminal,
        "verifierPassed": verifier_passed,
        "factComplete": fact_complete,
        "reviewEligible": review_eligible,
        "datasetReviewStatus": review_status,
        "trainingEligible": training_eligible,
        "facts": {
            "actionType": action_type or None,
            "actionProposed": action_proposed,
            "userConfirmed": user_confirmed,
            "remoteOutcomeKnown": remote_outcome_known,
            "actionOutcome": action_outcome or None,
            "caseCreated": case_created,
            "caseStatus": case_status or None,
            "humanResolved": human_resolved,
            "supportResolutionComplete": support_resolution_complete,
        },
    }


def recursive_subset_match(expected: Any, actual: Any, path: str = "$") -> list[str]:
    """Return deterministic mismatch descriptions for a recursive subset."""

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected object, got {type(actual).__name__}"]
        mismatches: list[str] = []
        for key, value in expected.items():
            child_path = f"{path}.{key}"
            if key not in actual:
                mismatches.append(f"{child_path}: missing")
                continue
            mismatches.extend(recursive_subset_match(value, actual[key], child_path))
        return mismatches
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [f"{path}: expected array, got {type(actual).__name__}"]
        if len(expected) > len(actual):
            return [f"{path}: expected at least {len(expected)} items, got {len(actual)}"]
        mismatches = []
        for index, value in enumerate(expected):
            mismatches.extend(recursive_subset_match(value, actual[index], f"{path}[{index}]"))
        return mismatches
    if expected != actual:
        return [f"{path}: expected {expected!r}, got {actual!r}"]
    return []


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _strict_bool(value: Any, *, default: bool) -> bool:
    return value if isinstance(value, bool) else default
