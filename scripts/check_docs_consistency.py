#!/usr/bin/env python3
"""Check current evidence claims and links across the project's public documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from check_evidence_manifest import DEFAULT_MANIFEST, REPO_ROOT, validate_repository


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return payload


def validate_documentation(payload: dict[str, Any]) -> list[str]:
    errors = validate_repository(DEFAULT_MANIFEST, require_current=False)
    expectations = payload.get("documentationConsistency")
    if not isinstance(expectations, list) or not expectations:
        return [*errors, "documentationConsistency must be a non-empty array"]

    for index, expectation in enumerate(expectations):
        label = f"documentationConsistency[{index}]"
        if not isinstance(expectation, dict):
            errors.append(f"{label} must be an object")
            continue
        relative = expectation.get("path")
        if not isinstance(relative, str) or not relative:
            errors.append(f"{label}.path is required")
            continue
        path = (REPO_ROOT / relative).resolve()
        try:
            path.relative_to(REPO_ROOT.resolve())
        except ValueError:
            errors.append(f"{label}.path escapes repository: {relative}")
            continue
        if not path.is_file():
            errors.append(f"documentation file missing: {relative}")
            continue
        text = path.read_text(encoding="utf-8")
        for token in expectation.get("requiredTokens") or []:
            if not isinstance(token, str) or not token:
                errors.append(f"{label}.requiredTokens must contain non-empty strings")
            elif token not in text:
                errors.append(f"missing current evidence token in {relative}: {token}")
        for token in expectation.get("forbiddenTokens") or []:
            if not isinstance(token, str) or not token:
                errors.append(f"{label}.forbiddenTokens must contain non-empty strings")
            elif token in text:
                errors.append(f"stale current evidence token in {relative}: {token}")
    return errors


def main() -> None:
    payload = _load_object(DEFAULT_MANIFEST)
    errors = validate_documentation(payload)
    if errors:
        raise SystemExit("documentation consistency check failed:\n- " + "\n- ".join(errors))
    print(
        "documentation consistency valid: "
        f"{len(payload['documentationConsistency'])} documents"
    )


if __name__ == "__main__":
    main()
