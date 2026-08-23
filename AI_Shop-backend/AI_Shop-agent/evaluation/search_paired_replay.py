"""Paired Search hard-negative replay against immutable v9 evidence.

This benchmark re-executes selected queries with the current Search runtime and
compares the result with the exact historical ranking.  It is deliberately
outside the final/current denominator: qrels, the v9 holdout, and the baseline
evidence are read-only inputs and are hash-checked before and after execution.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.adapters.search import run_search_case
from evaluation.core.catalog import load_catalog_fixture
from evaluation.core.contracts import EvaluationCase, Split
from evaluation.core.datasets import canonical_dataset_sha256, parse_case
from evaluation.core.evidence import verify_evidence
from evaluation.core.fingerprints import source_fingerprint
from evaluation.core.io import (
    AGENT_ROOT,
    EVIDENCE_ROOT,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    canonical_json_bytes,
    load_json,
    load_jsonl,
    relative_to_repo,
    sha256_bytes,
    sha256_file,
    utc_now,
)
from evaluation.core.metrics import ndcg_at_k, percentile, recall_at_k, reciprocal_rank_at_k

PAIRED_REPLAY_SCHEMA = "aishop-search-paired-replay/v1"
PAIRED_REPLAY_EVIDENCE_SCHEMA = "aishop-search-paired-replay-evidence/v1"
DEFAULT_BASELINE_EVIDENCE = EVIDENCE_ROOT
DEFAULT_V9_HOLDOUT = (
    AGENT_ROOT / "evaluation" / ".holdouts" / "final-holdout-20260822-ai-quality-v9.jsonl"
)
DEFAULT_CASE_IDS = (
    "search-fin-v9-11-office",
    "search-fin-v9-23-snack-100",
    "search-fin-v9-28-lip-100",
    "search-fin-v9-33-coat-no-outdoor",
    "search-fin-v9-34-snack-no-wangwang",
    "search-fin-v9-43-partial-headset",
    "search-fin-v9-44-partial-office",
    "search-fin-v9-47-compare-xm",
    "search-fin-v9-49-compare-lip",
    "search-fin-v9-50-compare-home",
)


class SearchPairedReplayError(ValueError):
    """Raised when a baseline/candidate comparison is not truly paired."""


def _portable_path(path: Path) -> str:
    try:
        return relative_to_repo(path)
    except ValueError:
        return str(path.resolve())


def _evaluation_user_id(run_id: str, case_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{case_id}".encode("utf-8")).hexdigest()
    return "ev" + digest[:13]


def _ranking(row: Mapping[str, Any]) -> list[str]:
    output = row.get("output") if isinstance(row.get("output"), Mapping) else {}
    raw = output.get("ranking") or []
    return [str(value) for value in raw if str(value)]


def _constraints(row: Mapping[str, Any]) -> dict[str, Any]:
    output = row.get("output") if isinstance(row.get("output"), Mapping) else {}
    value = output.get("constraints")
    return dict(value) if isinstance(value, Mapping) else {}


def _metric_values(ranking: Sequence[str], qrels: Mapping[str, int]) -> dict[str, float]:
    return {
        "recallAt10": recall_at_k(ranking, dict(qrels), 10),
        "mrrAt10": reciprocal_rank_at_k(ranking, dict(qrels), 10),
        "ndcgAt10": ndcg_at_k(ranking, dict(qrels), 10),
    }


def _positive_ids(qrels: Mapping[str, int]) -> set[str]:
    return {str(product_id) for product_id, grade in qrels.items() if int(grade) > 0}


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _aggregate(rows: Sequence[Mapping[str, Any]], side: str) -> dict[str, Any]:
    measured = [row for row in rows if isinstance(row.get(side), Mapping)]
    metrics = {
        name: round(
            sum(float(row[side][name]) for row in measured) / len(measured), 6
        )
        if measured
        else None
        for name in ("recallAt10", "mrrAt10", "ndcgAt10")
    }
    positive_total = sum(len(row.get("positiveIds") or []) for row in measured)
    recovered = sum(
        len(set((row.get(f"{side}Ranking") or [])[:10]) & set(row.get("positiveIds") or []))
        for row in measured
    )
    metrics["recallAt10Micro"] = (
        round(recovered / positive_total, 6) if positive_total else None
    )
    metrics["recallAt10MicroNumerator"] = recovered
    metrics["recallAt10MicroDenominator"] = positive_total
    return metrics


def build_paired_replay_report(
    *,
    baseline_rows: Mapping[str, Mapping[str, Any]],
    candidate_rows: Mapping[str, Mapping[str, Any]],
    cases: Sequence[EvaluationCase],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)) or not case_ids:
        raise SearchPairedReplayError("paired replay requires unique non-empty cases")
    if set(baseline_rows) != set(case_ids) or set(candidate_rows) != set(case_ids):
        raise SearchPairedReplayError("baseline, candidate, and selected case sets differ")

    comparisons: list[dict[str, Any]] = []
    badcases: list[dict[str, Any]] = []
    for case in cases:
        case_id = case.case_id
        baseline = baseline_rows[case_id]
        candidate = candidate_rows[case_id]
        query = str(case.input.get("query") or "")
        constraints = dict(case.input.get("constraints") or {})
        expected = case.expected
        qrels = {str(key): int(value) for key, value in expected.get("qrels", {}).items()}
        if str((baseline.get("output") or {}).get("query") or "") != query:
            raise SearchPairedReplayError(f"{case_id}: baseline query differs from holdout")
        if _constraints(baseline) != constraints:
            raise SearchPairedReplayError(f"{case_id}: baseline constraints differ from holdout")
        if str((candidate.get("output") or {}).get("query") or "") != query:
            raise SearchPairedReplayError(f"{case_id}: candidate query differs from holdout")
        if _constraints(candidate) != constraints:
            raise SearchPairedReplayError(f"{case_id}: candidate constraints differ from holdout")
        baseline_ranking = _ranking(baseline)
        candidate_ranking = _ranking(candidate)
        baseline_metrics = _metric_values(baseline_ranking, qrels)
        candidate_metrics = _metric_values(candidate_ranking, qrels)
        delta = {
            name: round(candidate_metrics[name] - baseline_metrics[name], 6)
            for name in baseline_metrics
        }
        positives = _positive_ids(qrels)
        baseline_hits = set(baseline_ranking[:10]) & positives
        candidate_hits = set(candidate_ranking[:10]) & positives
        recovered_ids = sorted(candidate_hits - baseline_hits)
        dropped_ids = sorted(baseline_hits - candidate_hits)
        remaining_misses = sorted(positives - candidate_hits)
        baseline_irrelevant = [value for value in baseline_ranking[:10] if value not in positives]
        candidate_irrelevant = [value for value in candidate_ranking[:10] if value not in positives]
        newly_introduced_irrelevant = sorted(
            set(candidate_irrelevant) - set(baseline_irrelevant)
        )
        candidate_metrics_raw = (
            candidate.get("metrics") if isinstance(candidate.get("metrics"), Mapping) else {}
        )
        constraint_violations = int(
            candidate_metrics_raw.get("constraintViolationCount") or 0
        )
        row = {
            "caseId": case_id,
            "slice": case.slice_tags[0] if case.slice_tags else "unlabeled",
            "query": query,
            "constraints": constraints,
            "qrels": qrels,
            "qrelsSha256": sha256_bytes(canonical_json_bytes(qrels)),
            "positiveIds": sorted(positives),
            "baselineRanking": baseline_ranking,
            "candidateRanking": candidate_ranking,
            "baseline": baseline_metrics,
            "candidate": candidate_metrics,
            "delta": delta,
            "recoveredRelevantIds": recovered_ids,
            "droppedRelevantIds": dropped_ids,
            "remainingMissedRelevantIds": remaining_misses,
            "newlyIntroducedIrrelevantIds": newly_introduced_irrelevant,
            "constraintViolationCount": constraint_violations,
            "baselineLatencyMs": _safe_float(baseline.get("latency_ms")),
            "candidateLatencyMs": _safe_float(candidate.get("latency_ms")),
            "candidateStatus": str(candidate.get("status") or ""),
            "candidateProviders": candidate.get("providers") or {},
            "candidateTrace": (candidate.get("output") or {}).get("trace") or [],
            "candidateError": candidate.get("error"),
        }
        comparisons.append(row)
        reasons: list[str] = []
        if remaining_misses:
            reasons.append("RECALL_MISS_REMAINS")
        if any(value < -1e-9 for value in delta.values()):
            reasons.append("PAIRED_RANKING_REGRESSION")
        if dropped_ids:
            reasons.append("DROPPED_RELEVANT_RESULT")
        if newly_introduced_irrelevant:
            reasons.append("NEW_IRRELEVANT_RESULT")
        if constraint_violations:
            reasons.append("HARD_CONSTRAINT_VIOLATION")
        if candidate.get("error") or str(candidate.get("status") or "") == "ERROR":
            reasons.append("CANDIDATE_EXECUTION_ERROR")
        if reasons:
            badcases.append(
                {
                    "caseId": case_id,
                    "reasons": reasons,
                    "query": query,
                    "delta": delta,
                    "recoveredRelevantIds": recovered_ids,
                    "droppedRelevantIds": dropped_ids,
                    "remainingMissedRelevantIds": remaining_misses,
                    "newlyIntroducedIrrelevantIds": newly_introduced_irrelevant,
                    "constraintViolationCount": constraint_violations,
                }
            )

    baseline_aggregate = _aggregate(comparisons, "baseline")
    candidate_aggregate = _aggregate(comparisons, "candidate")
    delta_aggregate = {
        name: round(float(candidate_aggregate[name]) - float(baseline_aggregate[name]), 6)
        if candidate_aggregate.get(name) is not None and baseline_aggregate.get(name) is not None
        else None
        for name in ("recallAt10", "recallAt10Micro", "mrrAt10", "ndcgAt10")
    }
    candidate_latencies = [
        float(row["candidateLatencyMs"])
        for row in comparisons
        if row.get("candidateLatencyMs") is not None
    ]
    return {
        "schemaVersion": PAIRED_REPLAY_SCHEMA,
        "runId": provenance.get("runId"),
        "status": "AUXILIARY_PAIRED_REPLAY",
        "releaseGateEligible": False,
        "normalQualityDenominatorExcluded": True,
        "baselineFinalModified": False,
        "qrelsModified": False,
        "createdAt": utc_now(),
        "provenance": dict(provenance),
        "caseCount": len(comparisons),
        "metrics": {
            "baseline": baseline_aggregate,
            "candidate": candidate_aggregate,
            "delta": delta_aggregate,
            "candidateConstraintViolationCount": sum(
                int(row["constraintViolationCount"]) for row in comparisons
            ),
            "candidateLatencyMs": {
                "sampleCount": len(candidate_latencies),
                "p50": round(percentile(candidate_latencies, 0.5), 3)
                if candidate_latencies
                else None,
                "p95": round(percentile(candidate_latencies, 0.95), 3)
                if candidate_latencies
                else None,
                "boundary": "LOCAL_FULL_STACK_NOT_PRODUCTION_SLO",
            },
        },
        "comparisons": comparisons,
        "badcases": badcases,
        "limitations": [
            "This is a paired diagnostic on known v9 hard negatives and ranking tails, not a new unseen final.",
            "Provider nondeterminism may affect a single replay; every candidate trace and latency remains visible.",
            "The historical v9 score, rankings, qrels, and current evidence package are not rewritten.",
        ],
    }


def load_replay_cases(
    holdout_path: Path,
    *,
    case_ids: Sequence[str] = DEFAULT_CASE_IDS,
) -> tuple[list[EvaluationCase], list[EvaluationCase]]:
    all_cases = [
        parse_case(row, expected_split=Split.FINAL) for row in load_jsonl(holdout_path)
    ]
    selected_ids = tuple(dict.fromkeys(str(value) for value in case_ids if str(value)))
    index = {case.case_id: case for case in all_cases}
    missing = sorted(set(selected_ids) - set(index))
    if missing:
        raise SearchPairedReplayError(f"holdout is missing selected case IDs: {missing}")
    selected = [index[case_id] for case_id in selected_ids]
    if any(case.domain.value != "search" for case in selected):
        raise SearchPairedReplayError("paired replay accepts only Search cases")
    return all_cases, selected


async def run_search_paired_replay(
    *,
    baseline_evidence: Path,
    holdout_path: Path,
    run_id: str,
    preflight: Mapping[str, Any],
    case_ids: Sequence[str] = DEFAULT_CASE_IDS,
) -> dict[str, Any]:
    baseline_verification = verify_evidence(baseline_evidence)
    baseline_manifest = load_json(baseline_evidence / "evidence-manifest.json")
    baseline_run = baseline_manifest.get("run") or {}
    if str(baseline_run.get("runId") or "") != "final-20260822-ai-quality-v9":
        raise SearchPairedReplayError("baseline evidence is not the immutable v9 final")
    holdout_sha_before = sha256_file(holdout_path)
    all_cases, selected = load_replay_cases(holdout_path, case_ids=case_ids)
    dataset_sha = canonical_dataset_sha256(all_cases)
    if dataset_sha != str(baseline_run.get("datasetSha256") or ""):
        raise SearchPairedReplayError("holdout canonical hash differs from baseline final")
    catalog_sha = str(load_catalog_fixture().get("canonicalSha256") or "")
    expected_catalogs = {str(case.expected.get("catalogSha256") or "") for case in selected}
    if expected_catalogs != {catalog_sha}:
        raise SearchPairedReplayError("selected cases are not bound to the current locked catalog")
    baseline_all = {
        str(row.get("case_id") or ""): row
        for row in load_jsonl(baseline_evidence / "cases.jsonl")
        if str(row.get("domain") or "") == "search"
    }
    missing_baseline = sorted({case.case_id for case in selected} - set(baseline_all))
    if missing_baseline:
        raise SearchPairedReplayError(f"baseline evidence is missing cases: {missing_baseline}")

    candidate_fingerprint = source_fingerprint()
    candidate_rows: dict[str, dict[str, Any]] = {}
    for case in selected:
        try:
            result = await run_search_case(
                case,
                user_id=_evaluation_user_id(run_id, case.case_id),
            )
            candidate_rows[case.case_id] = result.public()
        except Exception as exc:
            candidate_rows[case.case_id] = {
                "case_id": case.case_id,
                "domain": "search",
                "status": "ERROR",
                "metrics": {},
                "latency_ms": 0.0,
                "output": {
                    "query": case.input.get("query"),
                    "constraints": dict(case.input.get("constraints") or {}),
                    "ranking": [],
                },
                "providers": {},
                "error": {"type": type(exc).__name__, "message": str(exc)[:500]},
            }
    holdout_sha_after = sha256_file(holdout_path)
    if holdout_sha_after != holdout_sha_before:
        raise SearchPairedReplayError("holdout changed during paired replay")
    selected_qrels = {
        case.case_id: {
            str(key): int(value) for key, value in case.expected.get("qrels", {}).items()
        }
        for case in selected
    }
    provenance = {
        "runId": run_id,
        "baselineEvidencePath": _portable_path(baseline_evidence),
        "baselineEvidenceSha256SumsSha256": baseline_verification[
            "sha256SumsSha256"
        ],
        "baselineRunId": baseline_run.get("runId"),
        "baselineDatasetSha256": baseline_run.get("datasetSha256"),
        "baselineSourceFingerprintSha256": sha256_bytes(
            canonical_json_bytes(baseline_run.get("sourceFingerprint") or {})
        ),
        "candidateSourceFingerprint": candidate_fingerprint,
        "candidateSourceFingerprintSha256": sha256_bytes(
            canonical_json_bytes(candidate_fingerprint)
        ),
        "holdoutPath": _portable_path(holdout_path),
        "holdoutFileSha256": holdout_sha_before,
        "holdoutCanonicalSha256": dataset_sha,
        "selectedQrelsSha256": sha256_bytes(canonical_json_bytes(selected_qrels)),
        "catalogSha256": catalog_sha,
        "selectedCaseIds": [case.case_id for case in selected],
        "preflight": dict(preflight),
    }
    return build_paired_replay_report(
        baseline_rows={case.case_id: baseline_all[case.case_id] for case in selected},
        candidate_rows=candidate_rows,
        cases=selected,
        provenance=provenance,
    )


def render_paired_replay_markdown(report: Mapping[str, Any]) -> str:
    metrics = report.get("metrics") or {}
    baseline = metrics.get("baseline") or {}
    candidate = metrics.get("candidate") or {}
    delta = metrics.get("delta") or {}
    lines = [
        "# Search hard-negative 成对回放",
        "",
        f"> `{report.get('status')}`；仅为辅助诊断，不修改 v9 final，不进入正常质量分母。",
        "",
        f"Run：`{report.get('runId')}`；case：`{report.get('caseCount')}`；baseline：`{(report.get('provenance') or {}).get('baselineRunId')}`。",
        "",
        "| 指标 | v9 baseline | current candidate | delta |",
        "|---|---:|---:|---:|",
    ]
    for name in ("recallAt10", "recallAt10Micro", "mrrAt10", "ndcgAt10"):
        lines.append(
            f"| `{name}` | {baseline.get(name)} | {candidate.get(name)} | {delta.get(name)} |"
        )
    lines.extend(
        [
            "",
            "## Badcase",
            "",
            "| Case | 原因 | Recall Δ | MRR Δ | NDCG Δ | 未召回 |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for row in report.get("badcases") or []:
        row_delta = row.get("delta") or {}
        lines.append(
            f"| `{row.get('caseId')}` | {', '.join(row.get('reasons') or [])} | "
            f"{row_delta.get('recallAt10')} | {row_delta.get('mrrAt10')} | "
            f"{row_delta.get('ndcgAt10')} | {', '.join(row.get('remainingMissedRelevantIds') or []) or '-'} |"
        )
    lines.extend(
        [
            "",
            "本地 P50/P95 仅描述本次完整链路回放，不是生产 SLO。每条 query、qrels hash、前后 ranking、Provider trace 与新引入负样本均保存在 `cases.jsonl`。",
            "",
        ]
    )
    return "\n".join(lines)


def _inventory(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in {"SHA256SUMS", "evidence-manifest.json"}
    }


def _sums(root: Path) -> str:
    values = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    }
    return "".join(f"{digest}  {name}\n" for name, digest in sorted(values.items()))


def _assert_output_boundary(path: Path) -> None:
    resolved = path.resolve()
    protected = [
        EVIDENCE_ROOT.resolve(),
        (EVIDENCE_ROOT.parent / "archive").resolve(),
    ]
    for root in protected:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        raise SearchPairedReplayError(f"paired replay cannot write inside {root}")


def write_paired_replay_evidence(report: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    _assert_output_boundary(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite paired replay evidence: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        atomic_write_json(staging / "report.json", report, overwrite=False)
        atomic_write_jsonl(
            staging / "cases.jsonl",
            [dict(row) for row in report.get("comparisons") or []],
            overwrite=False,
        )
        atomic_write_jsonl(
            staging / "badcases.jsonl",
            [dict(row) for row in report.get("badcases") or []],
            overwrite=False,
        )
        atomic_write_text(
            staging / "report.md",
            render_paired_replay_markdown(report),
            overwrite=False,
        )
        manifest = {
            "schemaVersion": PAIRED_REPLAY_EVIDENCE_SCHEMA,
            "kind": "search-paired-replay",
            "runId": report.get("runId"),
            "status": report.get("status"),
            "normalQualityDenominatorExcluded": True,
            "baselineFinalModified": False,
            "qrelsModified": False,
            "baselineRunId": (report.get("provenance") or {}).get("baselineRunId"),
            "baselineEvidenceSha256SumsSha256": (report.get("provenance") or {}).get(
                "baselineEvidenceSha256SumsSha256"
            ),
            "holdoutFileSha256": (report.get("provenance") or {}).get(
                "holdoutFileSha256"
            ),
            "selectedQrelsSha256": (report.get("provenance") or {}).get(
                "selectedQrelsSha256"
            ),
            "createdAt": utc_now(),
            "files": _inventory(staging),
        }
        atomic_write_json(staging / "evidence-manifest.json", manifest, overwrite=False)
        atomic_write_text(staging / "SHA256SUMS", _sums(staging), overwrite=False)
        for path in staging.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o444)
        verify_paired_replay_evidence(staging)
        staging.replace(output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_paired_replay_evidence(output_dir)


def verify_paired_replay_evidence(root: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = root / "evidence-manifest.json"
    sums_path = root / "SHA256SUMS"
    if not manifest_path.is_file() or not sums_path.is_file():
        raise SearchPairedReplayError("paired replay evidence is incomplete")
    expected: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name or name in expected:
            raise SearchPairedReplayError(f"invalid SHA256SUMS line: {line!r}")
        target = (root / name).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise SearchPairedReplayError("evidence inventory escapes package") from exc
        expected[name] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(expected) != actual:
        raise SearchPairedReplayError("paired replay file set differs from SHA256SUMS")
    for name, digest in expected.items():
        if sha256_file(root / name) != digest:
            raise SearchPairedReplayError(f"paired replay hash mismatch: {name}")
    manifest = load_json(manifest_path)
    if manifest.get("schemaVersion") != PAIRED_REPLAY_EVIDENCE_SCHEMA:
        raise SearchPairedReplayError("paired replay manifest schema is invalid")
    if not manifest.get("normalQualityDenominatorExcluded"):
        raise SearchPairedReplayError("paired replay must stay outside quality denominator")
    if manifest.get("baselineFinalModified") or manifest.get("qrelsModified"):
        raise SearchPairedReplayError("paired replay manifest claims a forbidden mutation")
    if manifest.get("files") != _inventory(root):
        raise SearchPairedReplayError("paired replay manifest inventory is stale")
    writable = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.stat().st_mode & 0o222
    ]
    if writable:
        raise SearchPairedReplayError(f"paired replay evidence is writable: {writable}")
    return {
        "verified": True,
        "root": str(root),
        "runId": manifest.get("runId"),
        "baselineRunId": manifest.get("baselineRunId"),
        "sha256SumsSha256": sha256_file(sums_path),
    }
