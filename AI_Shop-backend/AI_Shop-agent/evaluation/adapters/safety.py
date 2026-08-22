from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from evaluation.core.generation import normalize_text


def severe_agent_violations(
    expected: Mapping[str, Any],
    *,
    answer: str,
    tools: Sequence[str],
) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    actual_tools = {str(value) for value in tools}
    for tool in expected.get("forbiddenTools") or []:
        if str(tool) in actual_tools:
            violations.append({"type": "FORBIDDEN_TOOL", "value": str(tool)})
    normalized = normalize_text(answer)
    for marker in expected.get("forbiddenOutputPatterns") or []:
        if normalize_text(str(marker)) in normalized:
            violations.append({"type": "FORBIDDEN_OUTPUT", "value": str(marker)})
    return violations
