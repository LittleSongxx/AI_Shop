#!/usr/bin/env python3
"""Validate time-bounded vulnerability exceptions used by CI."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = REPO_ROOT / "security" / "dependency-exceptions.json"
CVE_PATTERN = re.compile(r"^CVE-\d{4}-\d{4,}$", re.IGNORECASE)


def _date(value: Any, field: str, index: int) -> date:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"exceptions[{index}].{field} must use YYYY-MM-DD") from exc


def validate_exceptions(payload: dict[str, Any], *, today: date | None = None) -> list[str]:
    errors: list[str] = []
    current = today or date.today()
    if payload.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")
    rows = payload.get("exceptions")
    if not isinstance(rows, list):
        return [*errors, "exceptions must be an array"]
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"exceptions[{index}] must be an object")
            continue
        cve = str(row.get("cve") or "").upper()
        if not CVE_PATTERN.fullmatch(cve):
            errors.append(f"exceptions[{index}].cve must be a CVE identifier")
        elif cve in seen:
            errors.append(f"duplicate exception: {cve}")
        seen.add(cve)
        for field in ("reason", "owner"):
            if not str(row.get(field) or "").strip():
                errors.append(f"exceptions[{index}].{field} is required")
        try:
            created_at = _date(row.get("createdAt"), "createdAt", index)
            expires_at = _date(row.get("expiresAt"), "expiresAt", index)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        lifetime = (expires_at - created_at).days
        if lifetime < 1 or lifetime > 90:
            errors.append(
                f"exceptions[{index}] must expire 1-90 days after createdAt"
            )
        if expires_at < current:
            errors.append(f"exceptions[{index}] expired on {expires_at.isoformat()}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    payload = json.loads(args.path.read_text(encoding="utf-8"))
    errors = validate_exceptions(payload)
    if errors:
        raise SystemExit("dependency exceptions are invalid:\n- " + "\n- ".join(errors))
    print(f"dependency exceptions valid: {len(payload['exceptions'])}")


if __name__ == "__main__":
    main()
