import hashlib
import json

from evaluation.verifier_mutation import self_check, write_report_package


def test_self_check_kills_mutations_and_preserves_benign_variants():
    report = self_check()

    assert report["classification"]["positiveClass"] == "MUTATION"
    assert report["mutation"]["killed"] == report["mutation"]["eligible"]
    assert {
        "matched_false",
        "non_authoritative",
        "opposite_conclusion",
        "failed_tool_success_claim",
    } <= {row["operator"] for row in report["mutation"]["operators"]}
    assert report["multiMutant"]["eligible"] == 1
    assert report["multiMutant"]["killed"] == 1
    assert report["multiMutant"]["monotonicAgainstSingleMutants"] is True
    assert report["benignInvariance"]["preserved"] == report["benignInvariance"]["eligible"]
    assert report["errors"] == []
    assert {row["status"] for row in report["unsupported"]} == {"NOT_RUN"}


def test_report_is_repeatable_and_checksums_do_not_include_themselves(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_report_package(self_check(), first)
    write_report_package(self_check(), second)

    assert (first / "report.json").read_bytes() == (second / "report.json").read_bytes()
    manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"] == {
        "report.json": {
            "bytes": (first / "report.json").stat().st_size,
            "sha256": hashlib.sha256((first / "report.json").read_bytes()).hexdigest(),
        }
    }
    sums = (first / "SHA256SUMS").read_text(encoding="utf-8")
    assert "  report.json\n" in sums
    assert "  manifest.json\n" in sums
    assert "SHA256SUMS" not in sums
