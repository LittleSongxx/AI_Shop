from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable

from evaluation.text2sql.dataset import load_cases, verify_human_gold
from evaluation.text2sql.io import (
    read_json,
    read_jsonl,
    sha256_file,
    utc_now,
    verify_sha256s,
    write_json,
    write_jsonl,
    write_sha256s,
)
from evaluation.text2sql.scoring import score_case, summarize

MetricReader = Callable[[dict[str, Any]], bool | None]


def _nested_passed(name: str, *, applicable: str | None = None) -> MetricReader:
    def read(score: dict[str, Any]) -> bool | None:
        value = score.get(name)
        if not isinstance(value, dict):
            return None
        if applicable and not value.get(applicable):
            return None
        return bool(value.get("passed"))

    return read


_PAIRED_METRICS: dict[str, MetricReader] = {
    "outcome": lambda score: bool(score.get("outcomePassed")),
    "completion": lambda score: bool(score.get("completionPassed")),
    "plan": _nested_passed("plan", applicable="applicable"),
    "sqlPlanConsistency": _nested_passed("sqlPlanConsistency", applicable="applicable"),
    "execution": lambda score: (
        bool((score.get("execution") or {}).get("executionPassed"))
        if (score.get("execution") or {}).get("applicable")
        else None
    ),
    "denotation": _nested_passed("denotation"),
    "narrative": _nested_passed("narrative"),
    "policy": _nested_passed("policy"),
    "flow": lambda score: (
        bool((score.get("flow") or {}).get("passed"))
        if (score.get("flow") or {}).get("applicable")
        else None
    ),
    "trustedRequest": lambda score: bool(score.get("trustedRequestPassed")),
    "ordinaryTrustedAnswer": lambda score: (
        bool(score.get("ordinaryTrustedAnswerPassed"))
        if score.get("ordinaryTrustedAnswerEligible")
        else None
    ),
}


def _load_evidence(root: Path, *, phase: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    verify_sha256s(root)
    manifest = read_json(root / "manifest.json")
    if manifest.get("phase") != phase:
        raise ValueError(f"expected {phase} evidence: {root}")
    if manifest.get("caseCount") != 80 or manifest.get("executionCount") != 240:
        raise ValueError(f"incomplete Text2SQL evidence: {root}")
    if manifest.get("canonicalTrial") != 1:
        raise ValueError(f"unexpected canonical trial: {root}")
    records = read_jsonl(root / "raw-responses.jsonl")
    if len(records) != 240:
        raise ValueError(f"raw response count is not 240: {root}")
    identities = [(row.get("caseId"), row.get("trial")) for row in records]
    if len(set(identities)) != 240:
        raise ValueError(f"duplicate case/trial evidence: {root}")
    if Counter(row.get("trial") for row in records) != Counter({1: 80, 2: 80, 3: 80}):
        raise ValueError(f"trial distribution mismatch: {root}")
    return manifest, records


def _rescore(case: Any, record: dict[str, Any]) -> dict[str, Any]:
    initial = record.get("initial") or {}
    source = record.get("sourceDataFingerprint") or {}
    score = score_case(
        case,
        dict(record.get("normalized") or {}),
        http_status=int(initial.get("httpStatus") or 0),
        trace=initial.get("trace"),
        flow=dict(record.get("flow") or {}),
        latency_ms=float(initial.get("latencyMs") or 0),
        fixture_unchanged=source.get("unchanged"),
    )
    score["trial"] = int(record.get("trial") or 0)
    score["canonical"] = bool(record.get("canonical"))
    return score


def _transition(before: bool | None, after: bool | None) -> str:
    if before is None or after is None:
        return "NOT_COMPARABLE"
    if before == after:
        return "UNCHANGED_PASS" if after else "UNCHANGED_FAIL"
    return "IMPROVED" if after else "REGRESSED"


def _rate_cell(summary: dict[str, Any], name: str) -> str:
    value = summary.get(name) or {}
    rate = value.get("rate")
    if rate is None:
        return "n/a"
    return f"{value.get('passed', 0)}/{value.get('eligible', 0)} ({rate:.1%})"


def _report_markdown(manifest: dict[str, Any]) -> str:
    pre = manifest["standardizedPreCanonical"]
    post = manifest["standardizedPostCanonical"]
    rows = [
        ("Outcome", "outcome"),
        ("Completion", "completion"),
        ("Plan", "plan"),
        ("Execution", "execution"),
        ("Denotation", "denotation"),
        ("Narrative", "narrative"),
        ("Flow", "flow"),
        ("Trusted request", "trustedRequest"),
    ]
    table = "\n".join(
        f"| {label} | {_rate_cell(pre, key)} | {_rate_cell(post, key)} |" for label, key in rows
    )
    checks = manifest["hardConditionChecks"]
    return (
        "# AI-Shop Text2SQL V0 前后配对证据\n\n"
        "本报告仅用于 DEVELOPMENT / PROVISIONAL 内部评测，不是生产准确率或发布结论。\n\n"
        "| 指标 | 修复前 canonical | 修复后 canonical |\n"
        "| --- | ---: | ---: |\n"
        f"{table}\n\n"
        "## 硬条件检查\n\n"
        f"- 两版均为 80×3：`{str(checks['completeExecutions']).lower()}`\n"
        f"- 修复后严重安全失败为 0：`{str(checks['zeroPostSevereSecurityFailures']).lower()}`\n"
        f"- DENY 源数据均未变化：`{str(checks['denyFixturesUnchanged']).lower()}`\n"
        "- 人工 canonical 输出评审：`PENDING_HUMAN_REVIEW`\n\n"
        "自动指标不构成发布门槛；最终答案判断以 A/B 双盲人工评审及必要的 C 仲裁为准。\n"
    )


def compare_baselines(
    pre: Path,
    post: Path,
    dataset: Path,
    output: Path,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    verify_human_gold(dataset)
    cases = {case.case_id: case for case in load_cases(dataset)}
    if len(cases) != 80:
        raise ValueError("paired comparison requires exactly 80 gold cases")
    pre_manifest, pre_records = _load_evidence(pre, phase="pre-foundation")
    post_manifest, post_records = _load_evidence(post, phase="post-foundation")
    pre_freeze = read_json(pre / "input-freeze/manifest.json")
    post_freeze = read_json(post / "input-freeze/manifest.json")
    dataset_hash = sha256_file(dataset)
    if {
        pre_freeze["dataset"]["sha256"],
        post_freeze["dataset"]["sha256"],
        dataset_hash,
    } != {dataset_hash}:
        raise ValueError("pre/post evidence is not bound to the same gold dataset")
    if pre_freeze.get("catalogSha256") != post_freeze.get("catalogSha256"):
        raise ValueError("pre/post catalog hash differs")
    if pre_freeze.get("fixture") != post_freeze.get("fixture"):
        raise ValueError("pre/post fixture fingerprint differs")
    stable_config_keys = (
        "llmBaseUrl",
        "llmModel",
        "llmFallbackModel",
        "llmTimeout",
        "llmMaxRetries",
        "llmPricingCnyPerMillion",
        "analyticsEvalFixedNow",
        "effectiveEvaluationFixedNow",
    )
    pre_config = pre_freeze.get("runtimeConfigRedacted") or {}
    post_config = post_freeze.get("runtimeConfigRedacted") or {}
    if any(pre_config.get(key) != post_config.get(key) for key in stable_config_keys):
        raise ValueError("pre/post model, provider, pricing, or evaluation clock differs")

    pre_scores = [_rescore(cases[row["caseId"]], row) for row in pre_records]
    post_scores = [_rescore(cases[row["caseId"]], row) for row in post_records]
    pre_canonical = {row["caseId"]: row for row in pre_scores if row["canonical"]}
    post_canonical = {row["caseId"]: row for row in post_scores if row["canonical"]}
    if set(pre_canonical) != set(cases) or set(post_canonical) != set(cases):
        raise ValueError("canonical evidence does not cover the gold case set")

    paired: list[dict[str, Any]] = []
    transition_counts: dict[str, Counter[str]] = {name: Counter() for name in _PAIRED_METRICS}
    for case_id in sorted(cases):
        before = pre_canonical[case_id]
        after = post_canonical[case_id]
        transitions = {}
        for name, reader in _PAIRED_METRICS.items():
            transition = _transition(reader(before), reader(after))
            transitions[name] = transition
            transition_counts[name][transition] += 1
        security_transition = _transition(
            not bool((before.get("security") or {}).get("severeFailure")),
            not bool((after.get("security") or {}).get("severeFailure")),
        )
        infrastructure_transition = _transition(
            not bool(before.get("infrastructureFailure")),
            not bool(after.get("infrastructureFailure")),
        )
        transition_counts.setdefault("security", Counter())[security_transition] += 1
        transition_counts.setdefault("infrastructure", Counter())[infrastructure_transition] += 1
        transitions["security"] = security_transition
        transitions["infrastructure"] = infrastructure_transition
        paired.append(
            {
                "caseId": case_id,
                "expectedOutcome": before.get("expectedOutcome"),
                "primaryView": before.get("primaryView"),
                "sliceTags": before.get("sliceTags") or [],
                "pre": {
                    "observedOutcome": before.get("observedOutcome"),
                    "observedCompletion": before.get("observedCompletion"),
                    "trustedRequestPassed": before.get("trustedRequestPassed"),
                    "infrastructureFailure": before.get("infrastructureFailure"),
                    "severeSecurityFailure": bool(
                        (before.get("security") or {}).get("severeFailure")
                    ),
                },
                "post": {
                    "observedOutcome": after.get("observedOutcome"),
                    "observedCompletion": after.get("observedCompletion"),
                    "trustedRequestPassed": after.get("trustedRequestPassed"),
                    "infrastructureFailure": after.get("infrastructureFailure"),
                    "severeSecurityFailure": bool(
                        (after.get("security") or {}).get("severeFailure")
                    ),
                },
                "transitions": transitions,
            }
        )

    post_deny_fingerprints = [
        row.get("sourceDataFingerprint")
        for row in post_records
        if cases[row["caseId"]].expected.outcome.value == "DENY"
    ]
    pre_summary_all = summarize(pre_scores)
    post_summary_all = summarize(post_scores)
    pre_summary_canonical = summarize(list(pre_canonical.values()))
    post_summary_canonical = summarize(list(post_canonical.values()))
    hard_checks = {
        "completeExecutions": len(pre_records) == len(post_records) == 240,
        "zeroPostSevereSecurityFailures": post_summary_all["severeSecurityFailures"] == 0,
        "denyFixturesUnchanged": len(post_deny_fingerprints) == 36
        and all((item or {}).get("unchanged") is True for item in post_deny_fingerprints),
        "goldHumanVerified": True,
        "sha256Verified": True,
        "humanCanonicalReviewComplete": False,
    }
    manifest = {
        "schemaVersion": "aishop-text2sql-paired-comparison/v0",
        "createdAt": utc_now(),
        "preEvidence": {
            "path": str(pre.resolve()),
            "manifestSha256": sha256_file(pre / "manifest.json"),
            "rawResponsesSha256": sha256_file(pre / "raw-responses.jsonl"),
            "originalSummary": pre_manifest.get("summaryCanonical"),
        },
        "postEvidence": {
            "path": str(post.resolve()),
            "manifestSha256": sha256_file(post / "manifest.json"),
            "rawResponsesSha256": sha256_file(post / "raw-responses.jsonl"),
            "originalSummary": post_manifest.get("summaryCanonical"),
        },
        "goldSha256": dataset_hash,
        "sameGoldCatalogFixtureModelProvider": True,
        "scoringPolicy": "both raw evidence sets rescored with this comparison package scorer",
        "standardizedPreAllTrials": pre_summary_all,
        "standardizedPostAllTrials": post_summary_all,
        "standardizedPreCanonical": pre_summary_canonical,
        "standardizedPostCanonical": post_summary_canonical,
        "pairedTransitions": {
            name: dict(sorted(counts.items())) for name, counts in sorted(transition_counts.items())
        },
        "hardConditionChecks": hard_checks,
        "development": True,
        "provisional": True,
        "unseen": False,
        "releaseGateEligible": False,
    }
    badcases = [
        row
        for row in paired
        if not row["post"]["trustedRequestPassed"] or "REGRESSED" in row["transitions"].values()
    ]
    output.mkdir(parents=True)
    write_jsonl(output / "paired-canonical.jsonl", paired)
    write_jsonl(output / "post-badcases.jsonl", badcases)
    write_json(output / "manifest.json", manifest)
    (output / "REPORT.md").write_text(_report_markdown(manifest), encoding="utf-8")
    write_sha256s(output)
    return {"output": str(output), **manifest}
