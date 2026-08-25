"""Run the small, service-independent AI_Shop project closure contract.

This is intentionally narrower than the full HTTP evaluation.  It exercises the
authoritative order evidence projection and response verifier with fixed cases
that represent the remaining v13 badcase classes.  The output is an immutable
project artifact outside ``evaluation-evidence``; it is not a production SLO or
a replacement for the live paired replay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AGENT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = AGENT_ROOT.parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from app.services.evidence_refs import (  # noqa: E402
    action_capability_ref,
    after_sales_eligibility_ref,
    order_card_fields_with_claims,
    order_refs,
)
from app.services.response_verifier import response_verifier  # noqa: E402

SCHEMA = "aishop-project-closure-contract/v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _order_evidence(*, include_item: bool = True) -> list[dict[str, Any]]:
    order: dict[str, Any] = {
        "order_id": "SM-CLOSURE-001",
        "order_status": 2,
        "order_status_name": "已发货",
        "amount": 3999,
        "order_time": "2026-08-25 00:00:00",
        "pay_scene": "在线支付",
        "pay_channel": "支付宝",
        "comment_status": 0,
    }
    if include_item:
        order["items"] = [
            {
                "order_id": "SM-CLOSURE-001",
                "order_item_id": "SM-CLOSURE-ITEM-001",
                "product_id": "P-CLOSURE-001",
                "product_name": "索尼 WH-1000XM6",
                "property_info": "黑色",
                "item_amount": 3999,
                "buy_count": 1,
                "order_item_status": 2,
            }
        ]
    return order_refs([order], captured="2026-08-25T00:00:00+00:00")


def _verify(
    assistant: str,
    business_sources: list[dict[str, Any]],
    *,
    order_resolution: str = "RESOLVED",
) -> dict[str, Any]:
    result = response_verifier.verify(
        assistant=assistant,
        biz_type="agent",
        tools_called=[],
        source_refs={
            "ragSources": [],
            "businessSources": business_sources,
        },
        rag_source_refs=[],
        order_resolution=order_resolution,
        has_pending_action=False,
    )
    return {
        "passed": result.passed,
        "action": result.action,
        "terminalQuality": result.terminal_quality,
        "issueCodes": [issue.code for issue in result.issues],
        "issueDetails": [issue.detail for issue in result.issues],
    }


def _case(
    case_id: str,
    description: str,
    expected_pass: bool,
    observed: dict[str, Any],
) -> dict[str, Any]:
    passed = observed.get("passed") is expected_pass
    return {
        "caseId": case_id,
        "description": description,
        "expectedPass": expected_pass,
        "observed": observed,
        "contractPassed": passed,
    }


def build_report() -> dict[str, Any]:
    order_sources = _order_evidence()
    order_only_sources = order_refs(
        [{"order_id": "SM-CLOSURE-001"}],
        captured="2026-08-25T00:00:00+00:00",
    )
    allowed = action_capability_ref(
        {
            "decision": "ALLOWED",
            "action": "CANCEL_ORDER",
            "orderId": "SM-CLOSURE-001",
            "capabilityVersion": "order-action-capability/v1",
            "evaluatedAt": "2026-08-25T00:00:00+00:00",
        }
    )
    denied = action_capability_ref(
        {
            "decision": "DENIED",
            "action": "CANCEL_ORDER",
            "orderId": "SM-CLOSURE-001",
            "capabilityVersion": "order-action-capability/v1",
            "evaluatedAt": "2026-08-25T00:00:00+00:00",
        }
    )
    refund = after_sales_eligibility_ref(
        {
            "decision": "ELIGIBLE",
            "decisionId": "after-sales-closure-001",
            "action": "REFUND",
            "orderId": "SM-CLOSURE-001",
            "orderItemId": "SM-CLOSURE-ITEM-001",
            "policyId": "refund-policy",
            "policyVersion": "v1",
            "evaluatedAt": "2026-08-25T00:00:00+00:00",
        }
    )
    assert allowed is not None and denied is not None and refund is not None

    cases = [
        _case(
            "order-status-claim",
            "订单状态必须由 Java status claim 支持",
            True,
            _verify("订单 SM-CLOSURE-001 当前已发货。", order_sources),
        ),
        _case(
            "order-status-without-field-claim",
            "只有订单号 claim 不能证明发货状态",
            False,
            _verify("订单 SM-CLOSURE-001 当前已发货。", order_only_sources),
        ),
        _case(
            "order-product-claim",
            "商品名必须匹配订单项 productName claim",
            True,
            _verify("订单 SM-CLOSURE-001 买的是索尼 WH-1000XM6。", order_sources),
        ),
        _case(
            "order-product-mismatch",
            "模型不能把其他商品名挂到该订单上",
            False,
            _verify("订单 SM-CLOSURE-001 买的是苹果手机。", order_sources),
        ),
        _case(
            "capability-allowed",
            "允许操作必须有匹配的 Java action capability",
            True,
            _verify(
                "订单 SM-CLOSURE-001 当前可以取消。",
                [*order_sources, allowed],
            ),
        ),
        _case(
            "capability-polarity-mismatch",
            "已拒绝的资格不能生成可操作结论",
            False,
            _verify(
                "订单 SM-CLOSURE-001 当前可以取消。",
                [*order_sources, denied],
            ),
        ),
        _case(
            "refund-versioned-policy",
            "退款资格必须绑定策略 ID、版本和评估时间",
            True,
            _verify(
                "订单 SM-CLOSURE-001 的订单项 SM-CLOSURE-ITEM-001 当前可以退款。",
                [*order_sources, refund],
            ),
        ),
        _case(
            "refund-unversioned-policy",
            "缺少版本化策略元数据时不得宣称退款资格",
            False,
            _verify(
                "订单 SM-CLOSURE-001 的订单项 SM-CLOSURE-ITEM-001 当前可以退款。",
                order_sources,
            ),
        ),
    ]

    mismatched_card = order_card_fields_with_claims(
        {
            "targetType": "ORDER_ITEM",
            "targetId": "SM-CLOSURE-ITEM-999",
            "orderId": "SM-CLOSURE-001",
            "orderItemId": "SM-CLOSURE-ITEM-999",
            "productName": "不属于该订单的商品",
        },
        order_sources,
    )
    cases.append(
        _case(
            "selection-item-ownership",
            "错订单项选择不得降级为订单卡",
            True,
            {
                "passed": mismatched_card == {},
                "card": mismatched_card,
            },
        )
    )

    return {
        "schemaVersion": SCHEMA,
        "claim": "CURRENT_SOURCE_CONTRACT_OBSERVATION",
        "notProductionSlo": True,
        "livePairedEvaluation": "NOT_RUN_DEPENDENCIES_NOT_READY",
        "sourceFingerprint": {
            "gitCommit": _git("rev-parse", "HEAD"),
            "worktreeStatus": _git("status", "--short"),
            "trackedDiffSha256": hashlib.sha256(
                _git("diff", "--binary").encode("utf-8")
            ).hexdigest(),
        },
        "environment": {
            "pythonCommand": "conda run -n shop python",
            "evaluationMode": "SERVICE_INDEPENDENT_UNIT_CONTRACT",
            "capturedAt": datetime.now(timezone.utc).isoformat(),
        },
        "summary": {
            "caseCount": len(cases),
            "passed": sum(bool(case["contractPassed"]) for case in cases),
            "failed": sum(not bool(case["contractPassed"]) for case in cases),
        },
        "cases": cases,
        "limitations": [
            "本报告只验证当前源码中的订单事实、资格和响应校验 contract，不代表线上答案正确率。",
            "完整客服 HTTP paired replay 需要 Java、MCP、Worker、MySQL、Redis、ES 和 Provider readiness。",
            "未在本报告中把历史 baseline 或旧人工评分迁移到当前候选版本。",
        ],
        "nextLiveCommand": (
            "cd AI_Shop-backend/AI_Shop-agent && conda run -n shop python -m evaluation.cli "
            "preflight --split regression"
        ),
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AI_Shop 项目收口 Contract 检查",
        "",
        f"- Schema: `{report['schemaVersion']}`",
        f"- Claim: `{report['claim']}`",
        f"- 当前源码 contract cases: `{report['summary']['passed']}/{report['summary']['caseCount']}`",
        "- 生产 SLO: `不适用`",
        "- 完整 live paired evaluation: `未运行，依赖未 ready`",
        "",
        "| Case | 预期 | 观察 | Contract |",
        "|---|---:|---:|---:|",
    ]
    for case in report["cases"]:
        observed = case["observed"]
        lines.append(
            f"| `{case['caseId']}` | {case['expectedPass']} | {observed.get('passed')} | "
            f"{'PASS' if case['contractPassed'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "## 限制",
            "",
            *[f"- {item}" for item in report["limitations"]],
            "",
            "## Live 评测入口",
            "",
            f"`{report['nextLiveCommand']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def write_report(output_dir: Path) -> None:
    if output_dir.exists():
        raise SystemExit(f"refusing to overwrite immutable output: {output_dir}")
    output_dir.mkdir(parents=True)
    report = build_report()
    report_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    manifest = {
        "schemaVersion": f"{SCHEMA}-evidence",
        "reportSchemaVersion": SCHEMA,
        "runId": output_dir.name,
        "status": "CONTRACT_PASSED_LIVE_EVALUATION_PENDING",
        "files": {
            "report.json": {"sha256": _sha256(report_path)},
            "report.md": {"sha256": _sha256(markdown_path)},
        },
    }
    manifest_path = output_dir / "evidence-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    files = [report_path, markdown_path, manifest_path]
    (output_dir / "SHA256SUMS").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in files),
        encoding="utf-8",
    )
    print(json.dumps({"outputDir": str(output_dir), **report["summary"]}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New output directory for the immutable closure evidence package",
    )
    args = parser.parse_args()
    write_report(args.output_dir.resolve())


if __name__ == "__main__":
    main()
