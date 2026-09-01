from pathlib import Path


START_SCRIPT = Path(__file__).resolve().parents[1] / "start.sh"


def test_local_start_exports_one_admin_assertion_secret_to_java_and_python() -> None:
    text = START_SCRIPT.read_text(encoding="utf-8")

    assert "AISHOP_ADMIN_ASSERTION_CURRENT_SECRET" in text
    assert 'AISHOP_ADMIN_ASSERTION_CURRENT_SECRET="$AISHOP_INTERNAL_TOKEN"' in text
