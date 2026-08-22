"""Authoritative state hashing and declarative Agent terminal-state checks."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from evaluation.core.io import canonical_json_bytes, sha256_bytes

_MISSING = object()


def state_hash(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _escape(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")


def structured_state_diff(before: Any, after: Any, *, path: str = "") -> list[dict[str, Any]]:
    """Return a deterministic RFC-6902-like diff with before/after values."""

    if isinstance(before, Mapping) and isinstance(after, Mapping):
        rows: list[dict[str, Any]] = []
        keys = sorted(set(before).union(after), key=str)
        for key in keys:
            child = f"{path}/{_escape(str(key))}"
            if key not in before:
                rows.append({"op": "add", "path": child, "before": None, "after": after[key]})
            elif key not in after:
                rows.append(
                    {"op": "remove", "path": child, "before": before[key], "after": None}
                )
            else:
                rows.extend(structured_state_diff(before[key], after[key], path=child))
        return rows
    if (
        isinstance(before, Sequence)
        and isinstance(after, Sequence)
        and not isinstance(before, (str, bytes, bytearray))
        and not isinstance(after, (str, bytes, bytearray))
    ):
        rows = []
        common = min(len(before), len(after))
        for index in range(common):
            rows.extend(structured_state_diff(before[index], after[index], path=f"{path}/{index}"))
        for index in range(common, len(before)):
            rows.append(
                {"op": "remove", "path": f"{path}/{index}", "before": before[index], "after": None}
            )
        for index in range(common, len(after)):
            rows.append(
                {"op": "add", "path": f"{path}/{index}", "before": None, "after": after[index]}
            )
        return rows
    if before != after:
        return [{"op": "replace", "path": path or "/", "before": before, "after": after}]
    return []


def _pointer(value: Any, path: str) -> Any:
    if path in {"", "/"}:
        return value
    cursor = value
    for raw in path.lstrip("/").split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cursor, Mapping) and part in cursor:
            cursor = cursor[part]
        elif isinstance(cursor, Sequence) and not isinstance(cursor, (str, bytes, bytearray)):
            try:
                cursor = cursor[int(part)]
            except (IndexError, TypeError, ValueError):
                return _MISSING
        else:
            return _MISSING
    return cursor


def evaluate_state_assertions(
    before: Any,
    after: Any,
    assertions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Evaluate explicit state contracts; unknown operators fail closed."""

    results: list[dict[str, Any]] = []
    for assertion in assertions:
        path = str(assertion.get("path") or "/")
        operator = str(assertion.get("operator") or assertion.get("op") or "equals")
        previous = _pointer(before, path)
        current = _pointer(after, path)
        expected = assertion.get("value")
        if operator == "unchanged":
            passed = previous is not _MISSING and previous == current
        elif operator == "equals":
            passed = current is not _MISSING and current == expected
        elif operator == "added":
            passed = previous is _MISSING and current is not _MISSING
        elif operator == "removed":
            passed = previous is not _MISSING and current is _MISSING
        elif operator == "changed":
            passed = previous is not _MISSING and current is not _MISSING and previous != current
        elif operator == "delta":
            try:
                passed = float(current) - float(previous) == float(expected)
            except (TypeError, ValueError):
                passed = False
        else:
            passed = False
        results.append(
            {
                "path": path,
                "operator": operator,
                "passed": passed,
                "expected": expected,
                "before": None if previous is _MISSING else previous,
                "after": None if current is _MISSING else current,
                **({"reason": "UNSUPPORTED_OPERATOR"} if operator not in {"unchanged", "equals", "added", "removed", "changed", "delta"} else {}),
            }
        )
    return results


def build_state_evidence(
    before: Any,
    after: Any,
    *,
    assertions: Sequence[Mapping[str, Any]] = (),
    read_only: bool = False,
) -> dict[str, Any]:
    changes = structured_state_diff(before, after)
    assertion_results = evaluate_state_assertions(before, after, assertions)
    if read_only:
        assertion_results.insert(
            0,
            {
                "path": "/",
                "operator": "unchanged",
                "passed": not changes,
                "expected": "EMPTY_DIFF",
                "before": None,
                "after": None,
            },
        )
    return {
        "beforeHash": state_hash(before),
        "afterHash": state_hash(after),
        "changes": changes,
        "changeCount": len(changes),
        "assertions": assertion_results,
        "matched": all(item["passed"] for item in assertion_results),
        "readOnly": bool(read_only),
    }


def duplicate_side_effect_count(
    effects: Sequence[Mapping[str, Any]],
    *,
    signature_fields: tuple[str, ...] = ("type", "businessKey", "resourceId"),
) -> int:
    """Count repeated durable effects by a stable business signature."""

    signatures: list[tuple[str, ...]] = []
    for effect in effects:
        signature = tuple(str(effect.get(field) or "") for field in signature_fields)
        if any(signature):
            signatures.append(signature)
    counts = Counter(signatures)
    return sum(max(0, count - 1) for count in counts.values())
