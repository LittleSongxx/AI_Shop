#!/usr/bin/env python3
"""Check links and claim-boundary tokens in the public portfolio documents."""

from __future__ import annotations

import re

from check_evidence_manifest import ROOT, validate_manifest

PUBLIC_DOCS = (
    ROOT / "README.md",
    ROOT / "docs/README.md",
    ROOT / "docs/architecture.md",
    ROOT / "docs/demo.md",
    ROOT / "docs/evaluation.md",
    ROOT / "docs/ownership.md",
)
LINK_RE = re.compile(r"\[[^]]+]\(([^)]+)\)")
REQUIRED_BOUNDARY_TOKENS = (
    "不代表生产容量",
    "没有真人用户",
    "不是真人试用",
)


def validate_documentation() -> list[str]:
    errors = validate_manifest()
    for path in PUBLIC_DOCS:
        if not path.is_file():
            errors.append(f"documentation file missing: {path.relative_to(ROOT)}")
            continue
        text = path.read_text(encoding="utf-8")
        for raw_target in LINK_RE.findall(text):
            target = raw_target.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.is_relative_to(ROOT.resolve()) or not resolved.exists():
                errors.append(
                    f"broken local link in {path.relative_to(ROOT)}: {raw_target}"
                )
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in PUBLIC_DOCS if path.is_file()
    )
    for token in REQUIRED_BOUNDARY_TOKENS:
        if token not in combined:
            errors.append(f"missing public claim boundary: {token}")
    return errors


def main() -> None:
    errors = validate_documentation()
    if errors:
        raise SystemExit("documentation consistency check failed:\n- " + "\n- ".join(errors))
    print(f"Documentation consistency OK ({len(PUBLIC_DOCS)} files)")


if __name__ == "__main__":
    main()
