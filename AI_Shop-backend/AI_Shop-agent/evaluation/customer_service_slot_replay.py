"""Immutable before/after evidence for deterministic customer-service slots."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.core.fingerprints import source_fingerprint
from evaluation.core.io import (
    EVIDENCE_ROOT,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    load_json,
    sha256_file,
    utc_now,
)
from evaluation.customer_service_gold import (
    HUMAN_STATUS,
    REPORT_SCHEMA,
    run_customer_service_gold,
)

SLOT_REPLAY_SCHEMA = "aishop-customer-service-slot-replay/v1"
SLOT_REPLAY_EVIDENCE_SCHEMA = "aishop-customer-service-slot-replay-evidence/v1"
SLOT_REPLAY_ROOT = EVIDENCE_ROOT.parent / "benchmarks" / "customer-service"
_METRICS = ("slotEntitySpanF1", "slotExactMatch")


class CustomerServiceSlotReplayError(ValueError):
    """Raised when a paired replay cannot preserve a valid baseline."""


def _metric(report: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = (report.get("metrics") or {}).get(name) or {}
    return {
        "value": value.get("value"),
        "numerator": value.get("numerator"),
        "denominator": value.get("denominator"),
        "badcaseIds": list(value.get("badcaseIds") or []),
        "confidenceInterval95": value.get("confidenceInterval95"),
    }


async def build_slot_replay(
    dataset_path: Path,
    *,
    baseline_report_path: Path,
    run_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    baseline = load_json(baseline_report_path)
    if baseline.get("schemaVersion") != REPORT_SCHEMA:
        raise CustomerServiceSlotReplayError("baseline is not a customer-service gold report")
    if baseline.get("status") != HUMAN_STATUS:
        raise CustomerServiceSlotReplayError("slot replay baseline must be HUMAN_VERIFIED")
    dataset_sha256 = sha256_file(dataset_path)
    if (baseline.get("dataset") or {}).get("sha256") != dataset_sha256:
        raise CustomerServiceSlotReplayError("baseline and replay dataset SHA-256 differ")
    current = await run_customer_service_gold(dataset_path, mode="rule")
    before_cases = {
        str(row.get("id") or ""): row for row in baseline.get("cases") or []
    }
    after_cases = {
        str(row.get("id") or ""): row for row in current.get("cases") or []
    }
    if not before_cases or set(before_cases) != set(after_cases):
        raise CustomerServiceSlotReplayError("baseline and replay case IDs differ")
    paired: list[dict[str, Any]] = []
    for case_id in sorted(before_cases):
        before = before_cases[case_id]
        after = after_cases[case_id]
        before_entities = dict((before.get("predicted") or {}).get("entities") or {})
        after_entities = dict((after.get("predicted") or {}).get("entities") or {})
        expected_slots = dict((after.get("expected") or {}).get("slots") or {})
        slot_applicable = bool(expected_slots)
        before_match = (
            bool((before.get("matches") or {}).get("slotExactMatch"))
            if slot_applicable
            else None
        )
        after_match = (
            bool((after.get("matches") or {}).get("slotExactMatch"))
            if slot_applicable
            else None
        )
        paired.append(
            {
                "caseId": case_id,
                "message": after.get("message"),
                "expectedSlots": expected_slots,
                "beforeEntities": before_entities,
                "afterEntities": after_entities,
                "predictionChanged": before_entities != after_entities,
                "beforeExactMatch": before_match,
                "afterExactMatch": after_match,
                "outcome": (
                    "NOT_APPLICABLE"
                    if not slot_applicable
                    else "FIXED"
                    if not before_match and after_match
                    else "REGRESSED"
                    if before_match and not after_match
                    else "RESIDUAL"
                    if not after_match
                    else "UNCHANGED_PASS"
                ),
            }
        )
    comparisons: dict[str, Any] = {}
    for name in _METRICS:
        before = _metric(baseline, name)
        after = _metric(current, name)
        before_bad = set(before["badcaseIds"])
        after_bad = set(after["badcaseIds"])
        comparisons[name] = {
            "before": before,
            "after": after,
            "absoluteDelta": round(float(after["value"]) - float(before["value"]), 6),
            "fixedCaseIds": sorted(before_bad - after_bad),
            "residualCaseIds": sorted(after_bad),
            "regressedCaseIds": sorted(after_bad - before_bad),
        }
    intent_before = _metric(baseline, "intentMacroF1")
    intent_after = _metric(current, "intentMacroF1")
    return (
        {
            "schemaVersion": SLOT_REPLAY_SCHEMA,
            "runId": run_id,
            "createdAt": utc_now(),
            "status": "PAIRED_REPLAY_COMPLETE",
            "dataset": {
                "path": str(dataset_path.resolve()),
                "sha256": dataset_sha256,
                "caseCount": len(paired),
                "annotationStatus": HUMAN_STATUS,
            },
            "baseline": {
                "reportPath": str(baseline_report_path.resolve()),
                "reportSha256": sha256_file(baseline_report_path),
                "resolverSourceSha256": (baseline.get("provenance") or {}).get(
                    "resolverSourceSha256"
                ),
            },
            "candidate": {
                "resolverSourceSha256": (current.get("provenance") or {}).get(
                    "resolverSourceSha256"
                ),
                "allowLlm": False,
            },
            "metrics": comparisons,
            "intentControl": {
                "before": intent_before,
                "after": intent_after,
                "unchanged": intent_before["value"] == intent_after["value"],
            },
            "pairedCaseCounts": dict(
                sorted(
                    {
                        outcome: sum(row["outcome"] == outcome for row in paired)
                        for outcome in {
                            "FIXED",
                            "REGRESSED",
                            "RESIDUAL",
                            "UNCHANGED_PASS",
                            "NOT_APPLICABLE",
                        }
                    }.items()
                )
            ),
            "releaseGateEligible": False,
            "normalQualityDenominatorExcluded": True,
            "limitations": [
                "Same-gold paired replay isolates a code change; it is not a new independent test set.",
                "The HUMAN_VERIFIED labels and historical report are read-only and were not modified.",
                "Raw amount formatting remains strict, so currency-prefix/suffix differences remain visible badcases.",
                "A perfect or near-perfect result on 60 fixed cases does not establish open-world accuracy.",
            ],
        },
        paired,
    )


def _render(report: Mapping[str, Any]) -> str:
    lines = [
        "# 客服槽位同集 Paired Replay",
        "",
        f"> `{report.get('runId')}`；相同 60 条人工金标，只隔离生产规则变化；不作为新 holdout。",
        "",
        "| 指标 | 优化前 | 优化后 | 绝对变化 | 修复 / 残余 / 回归 |",
        "|---|---:|---:|---:|---|",
    ]
    for name in _METRICS:
        metric = (report.get("metrics") or {}).get(name) or {}
        lines.append(
            f"| `{name}` | {(metric.get('before') or {}).get('value')} | "
            f"{(metric.get('after') or {}).get('value')} | {metric.get('absoluteDelta')} | "
            f"{len(metric.get('fixedCaseIds') or [])} / "
            f"{len(metric.get('residualCaseIds') or [])} / "
            f"{len(metric.get('regressedCaseIds') or [])} |"
        )
    residual = ((report.get("metrics") or {}).get("slotExactMatch") or {}).get(
        "residualCaseIds"
    ) or []
    lines.extend(
        [
            "",
            f"残余 strict-format badcase：`{', '.join(residual) or '无'}`。",
            "",
            "边界：该结果证明当前规则在同一人工金标上的改善，不证明未见请求、线上客服成功率或业务转化。",
            "",
        ]
    )
    return "\n".join(lines)


def write_slot_replay_evidence(
    report: Mapping[str, Any],
    paired_cases: Sequence[Mapping[str, Any]],
    *,
    package_id: str,
) -> tuple[Path, str]:
    if not package_id or any(char in package_id for char in "/\\"):
        raise CustomerServiceSlotReplayError("package_id must be path-safe")
    root = SLOT_REPLAY_ROOT / package_id
    if root.exists():
        raise FileExistsError(f"slot replay evidence already exists: {root}")
    root.mkdir(parents=True)
    fingerprint = source_fingerprint()
    payload = {**dict(report), "sourceFingerprint": fingerprint}
    atomic_write_json(root / "report.json", payload, overwrite=False)
    atomic_write_jsonl(root / "paired-cases.jsonl", paired_cases, overwrite=False)
    atomic_write_text(root / "report.md", _render(payload), overwrite=False)
    inventory = {
        path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
        for path in sorted(root.iterdir())
        if path.is_file()
    }
    manifest = {
        "schemaVersion": SLOT_REPLAY_EVIDENCE_SCHEMA,
        "reportSchemaVersion": SLOT_REPLAY_SCHEMA,
        "packageId": package_id,
        "runId": report.get("runId"),
        "datasetSha256": (report.get("dataset") or {}).get("sha256"),
        "baselineReportSha256": (report.get("baseline") or {}).get("reportSha256"),
        "sourceSha256": (fingerprint.get("source") or {}).get("sha256"),
        "providerConfigurationSha256": fingerprint.get("providerConfigurationSha256"),
        "normalQualityDenominatorExcluded": True,
        "files": inventory,
    }
    atomic_write_json(root / "evidence-manifest.json", manifest, overwrite=False)
    sums = {
        path.name: sha256_file(path)
        for path in sorted(root.iterdir())
        if path.is_file() and path.name != "SHA256SUMS"
    }
    atomic_write_text(
        root / "SHA256SUMS",
        "".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())),
        overwrite=False,
    )
    verify_slot_replay_evidence(root)
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    return root, sha256_file(root / "SHA256SUMS")


def verify_slot_replay_evidence(root: Path) -> dict[str, Any]:
    sums = root / "SHA256SUMS"
    if not root.is_dir() or not sums.is_file():
        raise CustomerServiceSlotReplayError(f"invalid slot replay root: {root}")
    expected: dict[str, str] = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name or name in expected:
            raise CustomerServiceSlotReplayError(f"invalid slot replay SHA line: {line!r}")
        expected[name] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(expected) != actual:
        raise CustomerServiceSlotReplayError("slot replay file set differs from SHA256SUMS")
    for name, digest in expected.items():
        if sha256_file(root / name) != digest:
            raise CustomerServiceSlotReplayError(f"slot replay hash mismatch: {name}")
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "evidence-manifest.json").read_text(encoding="utf-8"))
    if report.get("schemaVersion") != SLOT_REPLAY_SCHEMA:
        raise CustomerServiceSlotReplayError("slot replay report schema is invalid")
    if manifest.get("schemaVersion") != SLOT_REPLAY_EVIDENCE_SCHEMA:
        raise CustomerServiceSlotReplayError("slot replay manifest schema is invalid")
    if manifest.get("runId") != report.get("runId"):
        raise CustomerServiceSlotReplayError("slot replay run ID mismatch")
    if manifest.get("datasetSha256") != (report.get("dataset") or {}).get("sha256"):
        raise CustomerServiceSlotReplayError("slot replay dataset hash mismatch")
    if manifest.get("baselineReportSha256") != (report.get("baseline") or {}).get(
        "reportSha256"
    ):
        raise CustomerServiceSlotReplayError("slot replay baseline hash mismatch")
    return {
        "valid": True,
        "packageId": manifest.get("packageId"),
        "runId": report.get("runId"),
        "sha256SumsSha256": sha256_file(sums),
    }
