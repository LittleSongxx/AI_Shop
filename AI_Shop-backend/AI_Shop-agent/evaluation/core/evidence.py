from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from evaluation.core.config import load_suite
from evaluation.core.contracts import (
    EVIDENCE_SCHEMA_VERSION_V2,
    EVIDENCE_SCHEMA_VERSION_V3,
    SUPPORTED_EVIDENCE_SCHEMA_VERSIONS,
    SUPPORTED_RUN_SCHEMA_VERSIONS,
    RunRecord,
)
from evaluation.core.io import (
    EVIDENCE_ROOT,
    RUNS_ROOT,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    load_json,
    load_jsonl,
    sha256_file,
    utc_now,
)
from evaluation.core.redaction import redact


def _run_directory(run_id: str) -> Path:
    return RUNS_ROOT / run_id


def _manifest_files(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in {"SHA256SUMS", "evidence-manifest.json"}
    }


def _render_report(run: RunRecord) -> str:
    lines = [
        "# AI Shop AI evaluation",
        "",
        f"- Run: {run.run_id}",
        f"- Split: {run.split.value}",
        f"- Dataset SHA-256: {run.dataset_sha256}",
        f"- Execution mode: {run.environment.get('executionMode')}",
        f"- Overall gate: {'PASS' if run.gates.get('passed') else 'FAIL'}",
        "",
        "## Domain gates",
        "",
    ]
    for domain, passed in (run.gates.get("domainOutcomes") or {}).items():
        lines.append(f"- {domain}: {'PASS' if passed else 'FAIL'}")
    lines.extend(["", "## Metrics", ""])
    for domain, domain_summary in run.summary.get("domains", {}).items():
        lines.append(f"### {domain}")
        metrics = (domain_summary or {}).get("metrics") or {}
        for name, estimate in metrics.items():
            interval = estimate.get("interval")
            interval_text = ""
            if interval:
                interval_text = (
                    f", 95% CI [{interval.get('lower')}, {interval.get('upper')}]"
                    f" ({interval.get('method')})"
                )
            notes = estimate.get("notes") or []
            note_text = f", notes={','.join(notes)}" if notes else ""
            lines.append(
                f"- {name}: {estimate.get('value')} (n={estimate.get('sampleCount')}"
                f"{interval_text}{note_text})"
            )
        lines.append("")
    if run.summary.get("sliceMetrics"):
        lines.extend(["## Slice metrics", ""])
        for domain, slices in run.summary["sliceMetrics"].items():
            lines.append(f"### {domain}")
            for name, value in (slices or {}).items():
                gate = value.get("normalQualityGate") or {}
                lines.append(
                    f"- {name}: n={value.get('caseCount', 0)}, "
                    f"casePass={gate.get('casePassRate')}, "
                    f"constraintsZero={gate.get('constraintViolationsZero')}, "
                    f"providerComplete={gate.get('providerComplete')}"
                )
            lines.append("")
    repeated = run.summary.get("repeatedAgentMetrics") or {}
    if repeated and repeated.get("status") != "NOT_RUN":
        pass_key = f"pass^{repeated.get('k')}"
        lines.extend(
            [
                "## Repeated Agent evidence",
                "",
                f"- k={repeated.get('k')}; pass-power={repeated.get(pass_key)}",
                f"- critical workflow pass power={repeated.get('criticalWorkflowPassPower')}",
                f"- duplicate side effects={repeated.get('duplicateSideEffectCount')}",
                f"- state diff match rate={repeated.get('stateDiffMatchRate')}",
                "",
            ]
        )
    semantic = run.summary.get("semanticShadowMetrics") or {}
    if semantic:
        lines.extend(
            [
                "## RAG semantic shadow judge",
                "",
                f"- cases={semantic.get('caseCount')}; available={semantic.get('availableCount')}; "
                f"unavailable={semantic.get('unavailableCount')}; disagreements={semantic.get('disagreementCount')}",
                "- Shadow diagnostic only; it is not human ground truth and does not enter hard gates.",
                "",
            ]
        )
        lines.append("")
    lines.extend(
        [
            "## Interpretation boundary",
            "",
            "- All quality gates are domain hard gates; no weighted aggregate can hide a failure.",
            "- Every executed case must be PASSED; an individual FAILED or ERROR case fails its domain gate.",
            "- Provider or dependency absence is an execution failure, never a skip or pass.",
            "- Latency is LOCAL_FULL_STACK evidence and is not a production SLO.",
            "- P99 is descriptive when its eligible sample count is below 100.",
            "",
        ]
    )
    return "\n".join(lines)


def write_run_evidence(
    run: RunRecord,
    *,
    lifecycle: dict[str, Any] | None = None,
) -> tuple[Path, str]:
    root = _run_directory(run.run_id)
    if root.exists():
        raise FileExistsError(f"run evidence is immutable and already exists: {root}")
    root.mkdir(parents=True)
    redacted_cases = [redact(case.public()) for case in run.cases]
    bad_cases = [value for value in redacted_cases if value.get("status") in {"FAILED", "ERROR"}]
    atomic_write_jsonl(root / "cases.jsonl", redacted_cases, overwrite=False)
    if run.trials:
        atomic_write_jsonl(
            root / "trials.jsonl",
            [redact(trial.public()) for trial in run.trials],
            overwrite=False,
        )
    atomic_write_jsonl(root / "bad-cases.jsonl", bad_cases, overwrite=False)
    atomic_write_json(root / "summary.json", redact(run.summary), overwrite=False)
    atomic_write_json(root / "gates.json", redact(run.gates), overwrite=False)
    atomic_write_json(root / "environment.json", redact(run.environment), overwrite=False)
    atomic_write_json(
        root / "source-fingerprint.json",
        redact(run.source_fingerprint),
        overwrite=False,
    )
    if lifecycle is not None:
        atomic_write_json(root / "lifecycle.json", redact(lifecycle), overwrite=False)
    atomic_write_text(root / "report.md", _render_report(run), overwrite=False)
    suite = load_suite()
    evidence_schema = (
        EVIDENCE_SCHEMA_VERSION_V3
        if run.schema_version == "aishop-evaluation-run/v3"
        else EVIDENCE_SCHEMA_VERSION_V2
    )
    manifest = {
        "schemaVersion": evidence_schema,
        "createdAt": utc_now(),
        "run": redact(run.public(include_cases=False)),
        "methodologySources": suite.get("methodologySources") or [],
        "limitations": suite.get("limitations") or [],
        "files": _manifest_files(root),
    }
    atomic_write_json(root / "evidence-manifest.json", manifest, overwrite=False)
    sums = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    }
    atomic_write_text(
        root / "SHA256SUMS",
        "".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())),
        overwrite=False,
    )
    verify_evidence(root)
    return root, sha256_file(root / "SHA256SUMS")


def verify_evidence(root: Path = EVIDENCE_ROOT) -> dict[str, Any]:
    sums_path = root / "SHA256SUMS"
    if not sums_path.is_file():
        raise ValueError(f"missing SHA256SUMS in {root}")
    expected: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name:
            raise ValueError(f"invalid SHA256SUMS line: {line!r}")
        expected[name] = digest
    actual_names = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(expected) != actual_names:
        raise ValueError("evidence file set does not match SHA256SUMS")
    mismatches = {
        name: {"expected": digest, "actual": sha256_file(root / name)}
        for name, digest in expected.items()
        if sha256_file(root / name) != digest
    }
    if mismatches:
        raise ValueError(f"evidence hash mismatch: {mismatches}")
    manifest = load_json(root / "evidence-manifest.json")
    if manifest.get("schemaVersion") not in SUPPORTED_EVIDENCE_SCHEMA_VERSIONS:
        raise ValueError("evidence manifest schema is invalid")
    run = manifest.get("run")
    if not isinstance(run, dict) or run.get("schemaVersion") not in SUPPORTED_RUN_SCHEMA_VERSIONS:
        raise ValueError("evidence manifest run contract is invalid")

    required = {
        "bad-cases.jsonl",
        "cases.jsonl",
        "environment.json",
        "evidence-manifest.json",
        "gates.json",
        "report.md",
        "source-fingerprint.json",
        "summary.json",
    }
    if not required.issubset(actual_names):
        raise ValueError(f"evidence is missing required files: {sorted(required - actual_names)}")

    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, dict):
        raise ValueError("evidence manifest files contract is invalid")
    expected_manifest_files = {
        name: {
            "sha256": sha256_file(root / name),
            "bytes": (root / name).stat().st_size,
        }
        for name in actual_names
        if name != "evidence-manifest.json"
    }
    # SHA256SUMS is generated after the manifest and is therefore not part of
    # manifest.files. It still covers the manifest itself.
    expected_manifest_files.pop("SHA256SUMS", None)
    if manifest_files != expected_manifest_files:
        raise ValueError("evidence manifest file inventory does not match the package")

    summary = load_json(root / "summary.json")
    gates = load_json(root / "gates.json")
    environment = load_json(root / "environment.json")
    fingerprint = load_json(root / "source-fingerprint.json")
    for name, standalone, embedded in (
        ("summary", summary, run.get("summary")),
        ("gates", gates, run.get("gates")),
        ("environment", environment, run.get("environment")),
        ("source fingerprint", fingerprint, run.get("sourceFingerprint")),
    ):
        if standalone != embedded:
            raise ValueError(f"standalone {name} differs from the run manifest")
    if summary.get("runId") != run.get("runId"):
        raise ValueError("summary runId differs from the run manifest")
    if summary.get("split") != run.get("split"):
        raise ValueError("summary split differs from the run manifest")
    if summary.get("datasetSha256") != run.get("datasetSha256"):
        raise ValueError("summary dataset hash differs from the run manifest")

    cases = load_jsonl(root / "cases.jsonl")
    trials = load_jsonl(root / "trials.jsonl") if (root / "trials.jsonl").is_file() else []
    bad_cases = load_jsonl(root / "bad-cases.jsonl")
    case_ids = [str(case.get("case_id") or "") for case in cases]
    case_keys = [
        (str(case.get("case_id") or ""), str(case.get("trial_id") or ""))
        for case in cases
    ]
    if not case_ids or "" in case_ids or len(case_keys) != len(set(case_keys)):
        raise ValueError("case evidence IDs are empty or duplicated")
    expected_bad = [case for case in cases if case.get("status") in {"FAILED", "ERROR"}]
    if bad_cases != expected_bad:
        raise ValueError("bad-cases.jsonl is not an exact projection of failed/error cases")
    domain_case_count = sum(
        int(value.get("caseCount") or 0)
        for value in (summary.get("domains") or {}).values()
        if isinstance(value, dict)
    )
    if domain_case_count != len(cases):
        raise ValueError("summary domain case counts differ from case evidence")
    trial_ids = [
        (str(trial.get("case_id") or ""), str(trial.get("trial_id") or ""))
        for trial in trials
    ]
    if trials and (not all(case_id and trial_id for case_id, trial_id in trial_ids) or len(trial_ids) != len(set(trial_ids))):
        raise ValueError("trial evidence IDs are empty or duplicated")
    return {
        "verified": True,
        "root": str(root),
        "fileCount": len(expected),
        "runId": (manifest.get("run") or {}).get("runId"),
        "sha256SumsSha256": sha256_file(sums_path),
    }


def publish_current(run_root: Path) -> str:
    verification = verify_evidence(run_root)
    parent = EVIDENCE_ROOT.parent
    archive_root = parent / "archive"
    parent.mkdir(parents=True, exist_ok=True)
    if EVIDENCE_ROOT.exists():
        try:
            previous_manifest = load_json(EVIDENCE_ROOT / "evidence-manifest.json")
            previous_run = previous_manifest.get("run") or {}
            previous_lifecycle = (
                load_json(EVIDENCE_ROOT / "lifecycle.json")
                if (EVIDENCE_ROOT / "lifecycle.json").is_file()
                else {}
            )
            # The public archive path is keyed by the immutable final run ID,
            # matching the evidence package and project manifest.  Very old
            # packages without a run ID fall back to their release ID.
            archive_name = str(previous_run.get("runId") or "")
            if not archive_name:
                archive_name = str(previous_lifecycle.get("releaseId") or "")
            if not archive_name:
                archive_name = "archived-current"
        except (OSError, ValueError) as exc:
            raise ValueError("cannot identify existing current evidence for archive") from exc
        archive_root.mkdir(parents=True, exist_ok=True)
        archive_path = archive_root / archive_name
        if archive_path.exists():
            # An archive is immutable.  An existing identical directory is
            # idempotent; any different content is a lifecycle violation.
            existing = verify_evidence(archive_path)
            current_digest = verify_evidence(EVIDENCE_ROOT)["sha256SumsSha256"]
            if existing["sha256SumsSha256"] != current_digest:
                raise FileExistsError(f"immutable evidence archive already exists: {archive_path}")
        else:
            shutil.copytree(EVIDENCE_ROOT, archive_path)
            verify_evidence(archive_path)
            for path in archive_path.rglob("*"):
                if path.is_file():
                    os.chmod(path, 0o444)
    staging = Path(tempfile.mkdtemp(prefix=".current-", dir=parent))
    backup = parent / ".current-backup"
    shutil.rmtree(staging)
    shutil.copytree(run_root, staging)
    verify_evidence(staging)
    if backup.exists():
        shutil.rmtree(backup)
    if EVIDENCE_ROOT.exists():
        EVIDENCE_ROOT.replace(backup)
    try:
        staging.replace(EVIDENCE_ROOT)
    except Exception:
        if backup.exists() and not EVIDENCE_ROOT.exists():
            backup.replace(EVIDENCE_ROOT)
        raise
    shutil.rmtree(backup, ignore_errors=True)
    for path in EVIDENCE_ROOT.rglob("*"):
        if path.is_file():
            os.chmod(path, 0o444)
    return str(verification["sha256SumsSha256"])
