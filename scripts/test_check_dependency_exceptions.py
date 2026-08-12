from datetime import date

from check_dependency_exceptions import validate_exceptions


def test_empty_exception_list_is_valid():
    assert validate_exceptions(
        {"schemaVersion": 1, "exceptions": []}, today=date(2026, 8, 12)
    ) == []


def test_exception_requires_owner_reason_and_at_most_ninety_days():
    errors = validate_exceptions(
        {
            "schemaVersion": 1,
            "exceptions": [
                {
                    "cve": "CVE-2026-12345",
                    "reason": "",
                    "owner": "",
                    "createdAt": "2026-08-01",
                    "expiresAt": "2026-11-15",
                }
            ],
        },
        today=date(2026, 8, 12),
    )
    assert any("reason is required" in error for error in errors)
    assert any("owner is required" in error for error in errors)
    assert any("1-90 days" in error for error in errors)


def test_expired_exception_fails():
    errors = validate_exceptions(
        {
            "schemaVersion": 1,
            "exceptions": [
                {
                    "cve": "CVE-2026-12345",
                    "reason": "Temporary upstream wait",
                    "owner": "backend-owner",
                    "createdAt": "2026-06-01",
                    "expiresAt": "2026-07-01",
                }
            ],
        },
        today=date(2026, 8, 12),
    )
    assert any("expired" in error for error in errors)
