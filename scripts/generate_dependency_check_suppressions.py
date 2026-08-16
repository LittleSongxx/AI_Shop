#!/usr/bin/env python3
"""Generate OWASP Dependency-Check suppressions from reviewed exceptions."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from check_dependency_exceptions import DEFAULT_PATH, validate_exceptions

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "security" / "dependency-check-suppression.xml"
NAMESPACE = "https://jeremylong.github.io/DependencyCheck/dependency-suppression.1.3.xsd"


def generate_suppressions(payload: dict[str, object]) -> ET.ElementTree:
    errors = validate_exceptions(payload)
    if errors:
        raise ValueError("dependency exceptions are invalid:\n- " + "\n- ".join(errors))

    ET.register_namespace("", NAMESPACE)
    root = ET.Element(f"{{{NAMESPACE}}}suppressions")
    rows = sorted(
        payload["exceptions"],
        key=lambda row: (str(row["cve"]), str(row["packageUrlRegex"])),
    )
    for row in rows:
        suppress = ET.SubElement(
            root,
            f"{{{NAMESPACE}}}suppress",
            {"until": str(row["expiresAt"])},
        )
        notes = ET.SubElement(suppress, f"{{{NAMESPACE}}}notes")
        notes.text = (
            f"{row['reason']} Owner: {row['owner']}; "
            f"review by {row['expiresAt']}."
        )
        package_url = ET.SubElement(
            suppress,
            f"{{{NAMESPACE}}}packageUrl",
            {"regex": "true"},
        )
        package_url.text = str(row["packageUrlRegex"])
        cve = ET.SubElement(suppress, f"{{{NAMESPACE}}}cve")
        cve.text = str(row["cve"]).upper()
    ET.indent(root, space="  ")
    return ET.ElementTree(root)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    payload = json.loads(args.path.read_text(encoding="utf-8"))
    tree = generate_suppressions(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(args.output, encoding="utf-8", xml_declaration=True)
    print(
        f"generated {len(payload['exceptions'])} scoped suppressions: "
        f"{args.output}"
    )


if __name__ == "__main__":
    main()
