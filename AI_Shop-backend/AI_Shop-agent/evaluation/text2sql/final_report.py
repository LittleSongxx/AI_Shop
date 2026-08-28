from __future__ import annotations

from pathlib import Path
from typing import Any

from evaluation.text2sql.io import (
    read_json,
    sha256_file,
    utc_now,
    verify_sha256s,
    write_json,
    write_sha256s,
)

_BOUNDARY = {
    "development": True,
    "provisional": True,
    "unseen": False,
    "releaseGateEligible": False,
}

_METRICS = (
    "outcome",
    "completion",
    "plan",
    "execution",
    "denotation",
    "narrative",
    "flow",
    "trustedRequest",
    "ordinaryTrustedAnswer",
)


def _require_boundary(document: dict[str, Any], *, name: str) -> None:
    actual = {key: document.get(key) for key in _BOUNDARY}
    if actual != _BOUNDARY:
        raise ValueError(f"{name} release boundary mismatch: {actual}")


def _evidence_binding(directory: Path, *files: str) -> dict[str, Any]:
    binding: dict[str, Any] = {
        "path": str(directory.resolve()),
        "sha256SumsSha256": sha256_file(directory / "SHA256SUMS"),
    }
    for name in files:
        binding[f"{name}Sha256"] = sha256_file(directory / name)
    return binding


def _compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    compact = {name: summary.get(name) for name in _METRICS}
    compact.update(
        {
            "caseCount": summary.get("caseCount"),
            "infrastructureFailures": summary.get("infrastructureFailures"),
            "severeSecurityFailures": summary.get("severeSecurityFailures"),
            "threeRunStability": summary.get("threeRunStability"),
            "efficiency": summary.get("efficiency"),
        }
    )
    return compact


def _rate_cell(summary: dict[str, Any], metric: str) -> str:
    value = summary.get(metric) or {}
    eligible = int(value.get("eligible") or 0)
    passed = int(value.get("passed") or 0)
    rate = value.get("rate")
    return "n/a" if rate is None else f"{passed}/{eligible} ({float(rate):.1%})"


def _report_markdown(manifest: dict[str, Any]) -> str:
    pre = manifest["automatedMetrics"]["preCanonical"]
    post = manifest["automatedMetrics"]["postCanonical"]
    human = manifest["humanCanonicalReview"]
    human_pre = human["decisionCountsBySource"]["pre-foundation"]
    human_post = human["decisionCountsBySource"]["post-foundation"]
    rows = (
        ("Outcome", "outcome"),
        ("Completion", "completion"),
        ("Plan", "plan"),
        ("Execution", "execution"),
        ("Denotation", "denotation"),
        ("Narrative", "narrative"),
        ("Flow", "flow"),
        ("Trusted request", "trustedRequest"),
        ("Ordinary trusted answer", "ordinaryTrustedAnswer"),
    )
    metric_rows = "\n".join(
        f"| {label} | {_rate_cell(pre, key)} | {_rate_cell(post, key)} |" for label, key in rows
    )
    checks = manifest["hardConditionChecks"]
    check_rows = "\n".join(f"- {name}: `{str(value).lower()}`" for name, value in checks.items())
    stability = manifest["automatedMetrics"]["postAllTrials"]["threeRunStability"]
    return (
        "# AI-Shop Text2SQL V0 最终 DEVELOPMENT / PROVISIONAL 证据\n\n"
        "## 结论\n\n"
        "V0 基础契约、80×3 前后基线、配对比较、gold 人工审核和 canonical 输出 "
        "A/B/C 人工流程均已形成可校验证据链。该结论只表示本轮建设与评测流程完成，"
        "不表示生产准确率、unseen 泛化、发布就绪或财务口径可审计。\n\n"
        f"修复后 canonical 输出由真人接受 {human_post.get('ACCEPT', 0)}/80，仍有 "
        f"{human_post.get('REJECT', 0)}/80 被拒绝；因此不得进入质量发布门禁。"
        "本报告固定标记 `development=true`、`provisional=true`、`unseen=false`、"
        "`releaseGateEligible=false`。\n\n"
        "## 人工 canonical 答案评审\n\n"
        "| 版本 | ACCEPT | REJECT |\n"
        "| --- | ---: | ---: |\n"
        f"| 修复前 | {human_pre.get('ACCEPT', 0)} | {human_pre.get('REJECT', 0)} |\n"
        f"| 修复后 | {human_post.get('ACCEPT', 0)} | {human_post.get('REJECT', 0)} |\n\n"
        f"配对人工变化：`{human['pairedTransitions']}`。A/B 一致 155 项，C 仅仲裁 5 项。\n\n"
        "## 自动 canonical 指标\n\n"
        "| 指标 | 修复前 | 修复后 |\n"
        "| --- | ---: | ---: |\n"
        f"{metric_rows}\n\n"
        "修复后 240 次运行的严重安全失败为 0；三次运行 outcome 稳定性为 "
        f"{_rate_cell({'stability': stability.get('outcome')}, 'stability')}，完整决策稳定性为 "
        f"{_rate_cell({'stability': stability.get('fullDecision')}, 'stability')}。"
        "自动指标只作诊断，最终答案判断以上述人工评审为准。\n\n"
        "## 硬条件\n\n"
        f"{check_rows}\n\n"
        "## 仍需后续处理的质量问题\n\n"
        f"- 修复后仍有 {human_post.get('REJECT', 0)} 个 canonical 输出未获人工接受。\n"
        f"- 自动 ordinary trusted answer 仅 {_rate_cell(post, 'ordinaryTrustedAnswer')}。\n"
        f"- 修复后 240 次运行保留了 "
        f"{manifest['automatedMetrics']['postAllTrials']['infrastructureFailures']} 个基础设施或模型失败，"
        "未按跳过处理。\n"
        "- V0 未加入 Join、窗口函数、同比环比、正式财务口径、确定性 compiler 或 verified-query。\n"
        "- 不启动生产部署或公开 benchmark；普通 badcase 留待下一阶段路线选择。\n"
    )


def build_final_report(
    *,
    pre: Path,
    post: Path,
    paired: Path,
    gold: Path,
    answer_review: Path,
    verification: Path,
    output: Path,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    for directory in (pre, post, paired, gold, answer_review, verification):
        verify_sha256s(directory)

    pre_manifest = read_json(pre / "manifest.json")
    post_manifest = read_json(post / "manifest.json")
    paired_manifest = read_json(paired / "manifest.json")
    gold_evidence = read_json(gold / "evidence.json")
    answer_evidence = read_json(answer_review / "evidence.json")
    verification_evidence = read_json(verification / "verification.json")

    for name, document in (
        ("pre baseline", pre_manifest),
        ("post baseline", post_manifest),
        ("paired comparison", paired_manifest),
        ("gold", gold_evidence),
        ("answer review", answer_evidence),
    ):
        _require_boundary(document, name=name)
    if pre_manifest.get("phase") != "pre-foundation" or post_manifest.get("phase") != (
        "post-foundation"
    ):
        raise ValueError("unexpected baseline phase")
    for name, document in (("pre", pre_manifest), ("post", post_manifest)):
        if (
            document.get("caseCount") != 80
            or document.get("trialCount") != 3
            or document.get("executionCount") != 240
            or document.get("canonicalTrial") != 1
        ):
            raise ValueError(f"{name} baseline is incomplete")
    paired_checks = paired_manifest.get("hardConditionChecks") or {}
    required_paired_checks = (
        "completeExecutions",
        "zeroPostSevereSecurityFailures",
        "denyFixturesUnchanged",
        "goldHumanVerified",
        "sha256Verified",
    )
    if not all(paired_checks.get(name) is True for name in required_paired_checks):
        raise ValueError("paired comparison hard conditions are incomplete")
    if gold_evidence.get("status") != "HUMAN_REVIEWED_ADJUDICATED":
        raise ValueError("gold human workflow is incomplete")
    if gold_evidence.get("summary", {}).get("caseCount") != 80:
        raise ValueError("gold does not contain exactly 80 cases")
    if answer_evidence.get("status") not in (
        "HUMAN_REVIEWED_ADJUDICATED",
        "HUMAN_VERIFIED",
    ):
        raise ValueError("canonical answer human workflow is incomplete")
    decision_counts = answer_evidence.get("finalDecisionCountsByBlindedSource") or {}
    if set(decision_counts) != {"pre-foundation", "post-foundation"} or any(
        sum((counts or {}).values()) != 80 for counts in decision_counts.values()
    ):
        raise ValueError("canonical answer review does not cover 80 outputs per version")
    if answer_evidence.get("humanDecisionAuthority") is not True:
        raise ValueError("canonical answer review lacks human decision authority")
    if verification_evidence.get("relevantChecksPassed") is not True:
        raise ValueError("relevant test verification is incomplete")

    post_all = _compact_summary(post_manifest["summaryAllTrials"])
    post_canonical = _compact_summary(paired_manifest["standardizedPostCanonical"])
    pre_canonical = _compact_summary(paired_manifest["standardizedPreCanonical"])
    hard_checks = {
        "preAndPostEach80x3": True,
        "postSevereSecurityFailuresZero": post_all["severeSecurityFailures"] == 0,
        "denySourceDataUnchanged": paired_checks["denyFixturesUnchanged"],
        "goldHumanWorkflowComplete": True,
        "canonicalABCHumanWorkflowComplete": True,
        "relevantTestsPassed": True,
        "allEvidenceSha256Verified": True,
    }
    if not all(hard_checks.values()):
        raise ValueError(f"final hard condition failed: {hard_checks}")

    manifest = {
        "schemaVersion": "aishop-text2sql-final-report/v0",
        "createdAt": utc_now(),
        "status": "DEVELOPMENT_PROVISIONAL_EVIDENCE_COMPLETE",
        "scope": "existing ten governed analytics views and minimal RBAC",
        "qualityReleaseThresholdSet": False,
        "productionReadinessClaim": False,
        "humanCanonicalReview": {
            "status": answer_evidence["status"],
            "reviewers": answer_evidence.get("reviewers"),
            "adjudicator": answer_evidence.get("adjudicator"),
            "agreementCount": answer_evidence.get("agreementCount"),
            "disagreementCount": answer_evidence.get("disagreementCount"),
            "decisionCountsBySource": decision_counts,
            "pairedTransitions": answer_evidence.get("pairedHumanTransitions"),
            "humanDecisionAuthority": True,
            "aiAssistanceUsed": answer_evidence.get("aiAssistanceUsed"),
            "pureHumanUnaided": answer_evidence.get("pureHumanUnaided"),
        },
        "automatedMetrics": {
            "preCanonical": pre_canonical,
            "postCanonical": post_canonical,
            "postAllTrials": post_all,
            "pairedTransitions": paired_manifest.get("pairedTransitions"),
        },
        "hardConditionChecks": hard_checks,
        "evidenceBindings": {
            "pre": _evidence_binding(pre, "manifest.json", "raw-responses.jsonl"),
            "post": _evidence_binding(post, "manifest.json", "raw-responses.jsonl"),
            "paired": _evidence_binding(paired, "manifest.json", "paired-canonical.jsonl"),
            "gold": _evidence_binding(gold, "evidence.json", "gold-v0.jsonl"),
            "answerReview": _evidence_binding(
                answer_review, "evidence.json", "answer-review.adjudicated.jsonl"
            ),
            "verification": _evidence_binding(verification, "verification.json"),
        },
        **_BOUNDARY,
    }
    output.mkdir(parents=True)
    write_json(output / "manifest.json", manifest)
    (output / "REPORT.md").write_text(_report_markdown(manifest), encoding="utf-8")
    write_sha256s(output)
    return {"output": str(output), **manifest}
