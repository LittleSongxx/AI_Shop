"""Atomic evaluation artifact and baseline-lock persistence."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

from app.evaluation.contracts import EvaluationCaseResult, EvaluationRun


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_commit(repo_root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def workspace_sha256(repo_root: Path) -> str:
    tracked = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    ).stdout
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    digest = hashlib.sha256(tracked)
    for relative in sorted(untracked):
        path = repo_root / relative
        if not path.is_file():
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def environment_fingerprint() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "pid": os.getpid(),
    }


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


class EvaluationArtifactWriter:
    def __init__(self, results_root: Path, baselines_root: Path) -> None:
        self.results_root = results_root
        self.baselines_root = baselines_root

    def write_run(self, run: EvaluationRun) -> Path:
        run_dir = self.results_root / run.metadata.suite / run.metadata.run_id
        cases_jsonl = "\n".join(
            json.dumps(
                case.model_dump(by_alias=True, mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for case in run.cases
        )
        _atomic_write(run_dir / "cases.jsonl", cases_jsonl + "\n")
        _atomic_write(
            run_dir / "summary.json",
            json.dumps(
                run.model_dump(by_alias=True, mode="json"),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        _atomic_write(run_dir / "report.md", self._markdown_report(run))
        return run_dir

    def accept_baseline(self, run: EvaluationRun) -> Path:
        self._assert_complete(run.cases)
        lock = {
            "schemaVersion": run.metadata.schema_version,
            "suite": run.metadata.suite,
            "acceptedRunId": run.metadata.run_id,
            "acceptedAt": run.metadata.created_at.isoformat(),
            "gitCommit": run.metadata.git_commit,
            "workspaceSha256": run.metadata.workspace_sha256,
            "datasetSha256": run.metadata.dataset_sha256,
            "evidenceSource": run.metadata.evidence_source,
            "executionMode": run.metadata.execution_mode,
            "environment": run.metadata.environment,
            "model": run.metadata.model,
            "parameters": run.metadata.parameters,
            "metrics": run.summary,
        }
        path = self.baselines_root / f"{run.metadata.suite}.lock.json"
        _atomic_write(path, json.dumps(lock, ensure_ascii=False, indent=2) + "\n")
        return path

    @staticmethod
    def _assert_complete(cases: Iterable[EvaluationCaseResult]) -> None:
        rows = list(cases)
        if not rows:
            raise ValueError("baseline cannot be accepted from an empty run")
        unexecuted = [case.case_id for case in rows if not case.executed]
        errors = [case.case_id for case in rows if case.status == "ERROR"]
        empty_assertions = [case.case_id for case in rows if not case.assertions]
        if unexecuted:
            raise ValueError(f"baseline contains unexecuted cases: {unexecuted}")
        if errors:
            raise ValueError(f"baseline contains runtime errors: {errors}")
        if empty_assertions:
            raise ValueError(f"baseline contains cases without assertions: {empty_assertions}")

    @staticmethod
    def _markdown_report(run: EvaluationRun) -> str:
        summary = run.summary
        lines = [
            f"# {run.metadata.suite} evaluation",
            "",
            f"- Schema: `{run.metadata.schema_version}`",
            f"- Run: `{run.metadata.run_id}`",
            f"- Commit: `{run.metadata.git_commit}`",
            f"- Evidence source: `{run.metadata.evidence_source}`",
            f"- Execution mode: `{run.metadata.execution_mode}`",
            f"- Dataset SHA-256: `{run.metadata.dataset_sha256}`",
            f"- Cases: {summary.get('executedCount')}/{summary.get('caseCount')} executed",
            f"- Task success: {summary.get('taskSuccesses')}/{summary.get('executedCount')} "
            f"({summary.get('taskSuccessRate')})",
            f"- Critical safety violations: {summary.get('criticalSafetyViolationCount')}",
            f"- Cost: CNY {summary.get('costCny')}",
            "",
            "| Case | Subset | Status | Latency (ms) | Assertions |",
            "|---|---|---:|---:|---:|",
        ]
        for case in run.cases:
            passed = sum(assertion.passed for assertion in case.assertions)
            lines.append(
                f"| `{case.case_id}` | `{case.subset}` | {case.status} | "
                f"{case.latency_ms if case.latency_ms is not None else '-'} | "
                f"{passed}/{len(case.assertions)} |"
            )
        disclosure = (summary.get("sampleDisclosure") or {}).get("message")
        if disclosure:
            lines.extend(["", f"> {disclosure}"])
        return "\n".join(lines) + "\n"
