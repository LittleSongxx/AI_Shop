from datetime import date
from xml.etree import ElementTree

from scripts.check_dependency_exceptions import validate_exceptions
from scripts.generate_dependency_check_suppressions import (
    NAMESPACE,
    generate_suppressions,
)


def _payload(**overrides):
    row = {
        "cve": "CVE-2026-12345",
        "packageUrlRegex": r"^pkg:maven/example/library@.*$",
        "reason": "The advisory affects a server, not this client library.",
        "owner": "platform",
        "createdAt": "2026-08-16",
        "expiresAt": "2026-09-15",
    }
    row.update(overrides)
    return {"schemaVersion": 1, "exceptions": [row]}


def test_validation_requires_a_scoped_package_regex():
    payload = _payload(packageUrlRegex="")

    errors = validate_exceptions(payload, today=date(2026, 8, 16))

    assert "exceptions[0].packageUrlRegex is required" in errors


def test_validation_rejects_an_invalid_package_regex():
    payload = _payload(packageUrlRegex="[")

    errors = validate_exceptions(payload, today=date(2026, 8, 16))

    assert any("packageUrlRegex is invalid" in error for error in errors)


def test_generator_emits_expiring_package_scoped_suppression():
    tree = generate_suppressions(_payload())
    root = tree.getroot()
    suppress = root.find(f"{{{NAMESPACE}}}suppress")

    assert suppress is not None
    assert suppress.attrib["until"] == "2026-09-15"
    package_url = suppress.find(f"{{{NAMESPACE}}}packageUrl")
    assert package_url is not None
    assert package_url.attrib["regex"] == "true"
    assert package_url.text == r"^pkg:maven/example/library@.*$"
    assert suppress.findtext(f"{{{NAMESPACE}}}cve") == "CVE-2026-12345"

    ElementTree.tostring(root, encoding="unicode")
