"""Independent audit of customer-service human labels.

This report audits the *labeling process and evidence sufficiency*; it does not
rewrite either frozen final package and it does not create a new model gold
label.  In particular, a human reviewer can call an answer logically correct
while the recorded runtime evidence is insufficient to verify that claim.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from evaluation.core.io import atomic_write_json, atomic_write_text, load_json, load_jsonl
from evaluation.customer_service_human_data import (
    ANSWER_REVIEW_REPORT_PATH,
    ANSWER_SOURCE_REPORT_PATH,
    HUMAN_GOLD_PATH,
    load_adjudicated_answer_labels,
    load_human_adjudicated_gold,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
HUMAN_PACKAGE = (
    REPO_ROOT
    / "evaluation-evidence"
    / "benchmarks"
    / "customer-service"
    / "customer-service-human-v1-20260823"
)
ANSWER_PACKAGE = (
    REPO_ROOT
    / "evaluation-evidence"
    / "benchmarks"
    / "customer-service"
    / "customer-service-answer-review-v2-adjudicated-20260824"
)
DEFAULT_JSON = (
    PROJECT_ROOT / "docs" / "evaluation" / "customer-service" / "客服标注审计.json"
)
DEFAULT_MD = DEFAULT_JSON.with_suffix(".md")

_DERIVED_QUANTITY_HINTS = ("一个", "一件", "一只", "一份", "一台", "一部", "一款")
_MONEY_FIELDS = frozenset({"amount", "budget", "price", "minPrice", "maxPrice"})


def _norm_text(value: Any) -> str:
    """Normalize only for provenance checks; raw labels remain untouched."""

    return "".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _slot_provenance(message: str, key: str, value: Any) -> str:
    """Classify whether a reviewed slot is grounded in the input text.

    This is an audit signal, not a replacement for human adjudication.  A
    numeric money value may be written with a currency suffix, and a quantity
    of one may be explicitly expressed by a Chinese classifier phrase.
    """

    message_norm = _norm_text(message)
    value_norm = _norm_text(value)
    if value_norm and value_norm in message_norm:
        return "DIRECT_SPAN"
    if key in _MONEY_FIELDS:
        numeric = re.sub(r"[^0-9.]", "", value_norm)
        if numeric and numeric in message_norm:
            return "CURRENCY_OR_UNIT_NORMALIZED"
    if key == "quantity" and value_norm == "1" and any(
        hint in message for hint in _DERIVED_QUANTITY_HINTS
    ):
        return "DERIVED_ALLOWED_RULE"
    return "NOT_FOUND_IN_INPUT"


def _slot_provenance_audit(gold: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    field_counts: Counter[str] = Counter()
    cases: list[dict[str, Any]] = []
    unsupported_case_ids: list[str] = []
    derived_cases: list[dict[str, Any]] = []
    for row in gold:
        message = str((row.get("input") or {}).get("message") or "")
        findings = []
        for key, value in ((row.get("expected") or {}).get("slots") or {}).items():
            provenance = _slot_provenance(message, str(key), value)
            counts[provenance] += 1
            field_counts[str(key)] += 1
            finding = {
                "field": str(key),
                "value": value,
                "provenance": provenance,
            }
            findings.append(finding)
            if provenance == "DERIVED_ALLOWED_RULE":
                derived_cases.append({"caseId": row["id"], **finding})
        unsupported = [
            item for item in findings if item["provenance"] == "NOT_FOUND_IN_INPUT"
        ]
        if unsupported:
            unsupported_case_ids.append(str(row["id"]))
        if findings and (unsupported or any(
            item["provenance"] != "DIRECT_SPAN" for item in findings
        )):
            cases.append(
                {
                    "caseId": row["id"],
                    "message": message,
                    "slots": findings,
                    "reviewRequired": bool(unsupported),
                }
            )
    return {
        "slotCount": sum(counts.values()),
        "provenanceCounts": dict(sorted(counts.items())),
        "fieldCounts": dict(sorted(field_counts.items())),
        "caseCountWithNonLiteralOrUnsupportedSlot": len(cases),
        "unsupportedCaseIds": unsupported_case_ids,
        "derivedAllowedCases": derived_cases,
        "cases": cases,
        "interpretation": (
            "NOT_FOUND_IN_INPUT 需要人工复核；DIRECT_SPAN 是原文可见槽位；"
            "CURRENCY_OR_UNIT_NORMALIZED 仅表示金额单位归一化；"
            "DERIVED_ALLOWED_RULE 只允许使用显式数量短语，不等于自由补全。"
        ),
    }


def _normal_amount(value: Any) -> str:
    text = str(value or "").strip().replace(",", "")
    for token in ("人民币", "元", "¥", "￥", "CNY"):
        text = text.replace(token, "")
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _factual_claim(answer: str) -> bool:
    text = str(answer or "").strip()
    if not text:
        return False
    # Refusals, clarification cards and handoff acknowledgements mention
    # business nouns but do not assert a runtime fact that needs a source.
    if any(
        phrase in text
        for phrase in (
            "无法确认",
            "不能给出确定",
            "请补充",
            "请联系人工",
            "已为您转接人工",
            '"type": "SHOPPING_CLARIFICATION"',
            "不客气",
        )
    ):
        return False
    # A negative lookup/result card is a runtime claim even when the result
    # list is empty; it must carry an authoritative negative source reference.
    if any(phrase in text for phrase in ("未找到", "没有在", "暂无", "当前没有符合", "暂未找到")):
        return True
    dynamic_nouns = ("价格", "库存", "在售", "订单状态", "物流状态", "退款状态", "优惠券")
    return any(noun in text for noun in dynamic_nouns) and any(
        marker in text for marker in ("是", "为", "有", "无", "没有", "当前", "显示", "状态：")
    )


def _load_intent_agreement() -> dict[str, Any]:
    path = HUMAN_PACKAGE / "merge.evidence.json"
    evidence = load_json(path)
    field_counts = Counter()
    for item in evidence.get("disagreements") or []:
        for field in item.get("fields") or []:
            field_counts[field] += 1
    case_count = int(evidence.get("caseCount") or 0)
    disagreement_count = int(evidence.get("disagreementCaseCount") or 0)
    field_counts = {
        str(field): int(count)
        for field, count in (evidence.get("fieldAgreementCounts") or {}).items()
    }

    def _label_value(row: dict[str, Any], field: str) -> str:
        value = (row.get("labels") or {}).get(field)
        if field == "slots":
            return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def _kappa(left: list[str], right: list[str]) -> float | None:
        if not left or len(left) != len(right):
            return None
        size = len(left)
        observed = sum(a == b for a, b in zip(left, right)) / size
        categories = sorted(set(left) | set(right))
        expected = sum(
            (left.count(category) / size) * (right.count(category) / size)
            for category in categories
        )
        if expected >= 1.0:
            return None
        return round((observed - expected) / (1.0 - expected), 6)

    field_stats: dict[str, dict[str, Any]] = {}
    review_a = evidence.get("reviewA") or {}
    review_b = evidence.get("reviewB") or {}
    review_a_path = REPO_ROOT / str(review_a.get("path") or "")
    review_b_path = REPO_ROOT / str(review_b.get("path") or "")
    if review_a_path.is_file() and review_b_path.is_file():
        rows_a = {str(row.get("id")): row for row in load_jsonl(review_a_path)}
        rows_b = {str(row.get("id")): row for row in load_jsonl(review_b_path)}
        for field in ("intent", "riskLevel", "shouldHandoff", "handoffSeverity", "slots"):
            pairs = [
                (_label_value(rows_a[case_id], field), _label_value(rows_b[case_id], field))
                for case_id in sorted(set(rows_a) & set(rows_b))
            ]
            left = [pair[0] for pair in pairs]
            right = [pair[1] for pair in pairs]
            field_stats[field] = {
                "caseCount": len(pairs),
                "agreementCount": sum(a == b for a, b in pairs),
                "agreementRate": round(sum(a == b for a, b in pairs) / len(pairs), 6)
                if pairs
                else None,
                "cohenKappa": _kappa(left, right),
            }
    adjudicated_reasons = [
        item
        for item in evidence.get("disagreements") or []
        if str(((item.get("adjudication") or {}).get("reason")) or "").strip()
    ]
    return {
        "caseCount": case_count,
        "disagreementCaseCount": disagreement_count,
        "exactAgreementCaseCount": int(evidence.get("exactAgreementCaseCount") or 0),
        "caseAgreementRate": round(
            (case_count - disagreement_count) / case_count, 6
        )
        if case_count
        else None,
        "fieldAgreementCounts": field_counts,
        "fieldStats": field_stats,
        "adjudicationReasonCoverage": {
            "disagreementCaseCount": disagreement_count,
            "reasonedCaseCount": len(adjudicated_reasons),
            "complete": len(adjudicated_reasons) == disagreement_count,
        },
        "interpretation": (
            "案件级完全一致要求 intent、risk、handoffSeverity、shouldHandoff 和 slots 全部一致；"
            "低案件级一致率主要由槽位 schema/格式分歧驱动，不能直接等同于意图标签错误。"
        ),
        "evidencePath": str(path.relative_to(REPO_ROOT)),
    }


def build_annotation_audit() -> dict[str, Any]:
    gold = load_human_adjudicated_gold()
    labels = load_adjudicated_answer_labels()
    answer_report = load_json(ANSWER_REVIEW_REPORT_PATH)
    source_report = load_json(ANSWER_SOURCE_REPORT_PATH)
    answer_cases = {
        str(item.get("caseId")): item
        for item in answer_report.get("cases") or []
        if isinstance(item, dict)
    }
    source_cases = {
        str(item.get("caseId")): item
        for item in source_report.get("cases") or []
        if isinstance(item, dict)
    }

    amount_style_cases: list[dict[str, Any]] = []
    for row in gold:
        slots = row.get("expected", {}).get("slots", {})
        if "amount" not in slots:
            continue
        raw = str(slots["amount"])
        normalized = _normal_amount(raw)
        if raw != normalized:
            amount_style_cases.append(
                {
                    "caseId": row["id"],
                    "message": row["input"]["message"],
                    "rawAmount": raw,
                    "normalizedAmount": normalized,
                    "issue": "金额槽位混用货币符号/元后缀；严格字符串 EM 会产生伪失败",
                }
            )

    taxonomy_cases = [
        {
            "caseId": "cs-gold-v1-011",
            "label": "REFUND",
            "runtimeConflict": "REFUND_STATUS",
            "status": "AMBIGUOUS",
            "reason": "退款政策/到账时长是规则咨询还是退款进度，当前意图枚举边界未写清。",
        },
        {
            "caseId": "cs-gold-v1-057",
            "label": "REFUND",
            "runtimeConflict": "REFUND_STATUS",
            "status": "AMBIGUOUS",
            "reason": "‘退款多久到账’缺少明确订单对象，标为退款动作与退款状态均有合理解释。",
        },
        {
            "caseId": "cs-gold-v1-044",
            "label": "PRODUCT_CONSULT",
            "runtimeConflict": "PRODUCT_SEARCH",
            "status": "TAXONOMY_GAP",
            "reason": "比较请求没有独立 PRODUCT_COMPARE 类别；当前标签与生产路由的可接受投影需要在规范中固定。",
        },
        {
            "caseId": "cs-gold-v1-049",
            "label": "CHAT",
            "runtimeConflict": "PAYMENT_ISSUE",
            "status": "TAXONOMY_GAP",
            "reason": "支付方式咨询不是支付故障；当前枚举缺少 PAYMENT_POLICY，归 CHAT 是可解释的兼容投影，但会掩盖该业务子类。",
        },
        {
            "caseId": "cs-gold-v1-056",
            "label": "DAMAGED_OR_WRONG_ITEM",
            "runtimeConflict": "AFTERSALES_UNKNOWN",
            "status": "BOUNDARY_REVIEW",
            "reason": "‘质量有问题想换货’足以落到收货异常，但是否保留泛售后类取决于业务路由优先级；仲裁理由充分，非明确误标。",
        },
        {
            "caseId": "cs-gold-v1-014",
            "label": "MEDIUM",
            "runtimeConflict": "LOW",
            "status": "POLICY_CHOICE",
            "reason": "确认收货会改变订单状态并影响售后边界；MEDIUM 是安全策略选择，不是可由文本唯一推出的客观标签。",
        },
        {
            "caseId": "cs-gold-v1-007",
            "label": "LOW",
            "runtimeConflict": "MEDIUM",
            "status": "POLICY_CHOICE",
            "reason": "未发货只表达履约进度，没有资金或安全损失；LOW 合理，但应在标注规范中固定延迟投诉的风险阈值。",
        },
        {
            "caseId": "cs-gold-v1-008",
            "label": "LOW",
            "runtimeConflict": "MEDIUM",
            "status": "POLICY_CHOICE",
            "reason": "物流停滞未等同丢件或损失；LOW 合理，但需明确何时升级为高风险/人工。",
        },
    ]

    evidence_status: Counter[str] = Counter()
    unverifiable_cases: list[dict[str, Any]] = []
    unverifiable_case_ids: list[str] = []
    na_fact_claim_cases: list[dict[str, Any]] = []
    for case_id, row in answer_cases.items():
        label = labels[case_id]["labels"]
        source_case = source_cases.get(case_id) or {}
        http = source_case.get("http") if isinstance(source_case.get("http"), dict) else {}
        answer = str(row.get("answer") or http.get("answer") or "")
        message = (
            source_case.get("message")
            or (source_case.get("input") or {}).get("message")
            or ((http.get("request") or {}).get("message") if isinstance(http.get("request"), dict) else None)
            or row.get("message")
        )
        citation = str(label.get("citationSupport") or "")
        if citation == "SUPPORTED":
            status = "VERIFIED_BY_VISIBLE_CITATION"
        elif citation == "UNSUPPORTED":
            status = "UNVERIFIABLE_RUNTIME_FACT"
            unverifiable_case_ids.append(case_id)
            if label.get("answerCorrect") is True:
                unverifiable_cases.append(
                    {
                        "caseId": case_id,
                        "message": message,
                        "answerExcerpt": answer[:240],
                        "answerCorrectLabel": True,
                        "reason": "人工认为回答逻辑正确，但固定 HTTP report 没有能直接证明具体事实的权威 sourceRef。",
                    }
                )
        elif citation == "NOT_APPLICABLE" and _factual_claim(answer):
            status = "RUBRIC_REVIEW_NEEDED"
            na_fact_claim_cases.append(
                {
                    "caseId": case_id,
                    "message": message,
                    "answerExcerpt": answer[:240],
                    "reason": "答案含事实性断言，但 citationSupport 标为 NOT_APPLICABLE；后续规范应允许 UNVERIFIABLE。",
                }
            )
        elif citation == "UNDECIDABLE":
            status = "UNDECIDABLE"
        else:
            status = "LOGICALLY_SUPPORTED_NO_CITATION_REQUIRED"
        evidence_status[status] += 1

    answer_agreement = answer_report.get("agreement") or {}
    return {
        "schemaVersion": "aishop-customer-service-annotation-audit/v1",
        "annotationAuditStatus": "COMPLETE_PROCESS_AUDIT_NOT_NEW_GOLD",
        "sourceArtifacts": {
            "intentGold": str(HUMAN_GOLD_PATH.relative_to(REPO_ROOT)),
            "intentHumanPackage": str(HUMAN_PACKAGE.relative_to(REPO_ROOT)),
            "answerReviewPackage": str(ANSWER_PACKAGE.relative_to(REPO_ROOT)),
            "answerReviewReport": str(ANSWER_REVIEW_REPORT_PATH.relative_to(REPO_ROOT)),
        },
        "intentSlotAudit": {
            "humanVerifiedCaseCount": len(gold),
            "reviewerAgreement": _load_intent_agreement(),
            "amountNormalizationInconsistency": {
                "caseCount": len(amount_style_cases),
                "cases": amount_style_cases,
                "recommendation": "评测增加 numeric/currency-normalized slot 指标；不要把单位格式差异写成抽取错误。",
            },
            "potentialTaxonomyDisputes": taxonomy_cases,
            "slotProvenanceAudit": _slot_provenance_audit(gold),
        },
        "answerReviewAudit": {
            "frozenReplayCaseCount": len(labels),
            "reviewerAgreement": answer_agreement,
            "labelEvidenceStatusCounts": dict(evidence_status),
            "unverifiableRuntimeFactCaseIds": unverifiable_case_ids,
            "unverifiableDespiteCorrectLabel": unverifiable_cases,
            "notApplicableFactClaimCases": na_fact_claim_cases,
            "interpretation": "UNVERIFIABLE 表示运行时证据不足，不等于模型一定答错；不能重新计入模型准确率。",
        },
        "annotationQualityAssessment": {
            "status": "REVIEWED_WITH_BOUNDARY_FLAGS",
            "humanLabelNotAutomaticallyOverruled": True,
            "confirmedMislabelCaseIds": [],
            "mislabelAssessment": (
                "未发现可由原文直接证伪的明确槽位或意图误标；当前风险主要是 taxonomy/风险策略边界和 schema 归一化，"
                "不能把仲裁标签当作专家不可争议真值。"
            ),
            "annotatorQualificationEvidence": "仓库仅保存 reviewer 身份与盲标/仲裁哈希，未保存客服领域资格证明；不能把本次结果描述为专家金标。",
            "intentAgreementWarning": "意图字段一致 57/60、风险字段一致 56/60；案件级完全一致 35/60 主要被槽位 schema/格式分歧拉低（槽位 45/60），另有 011/057/044 的 taxonomy 边界争议。仲裁结果可作为当前版本金标，但不是不可争议真值。",
            "suspectedTaxonomyCases": [item["caseId"] for item in taxonomy_cases],
            "normalizationOnlyCases": [item["caseId"] for item in amount_style_cases],
            "evidenceInsufficientButLogicalCorrectCases": [
                item["caseId"] for item in unverifiable_cases
            ],
            "recommendedExpertReview": [
                "REFUND 与 REFUND_STATUS 的‘多久到账’边界（011、057）",
                "PRODUCT_COMPARE 是否独立成类（044），PAYMENT_POLICY 是否独立于 CHAT（049）",
                "确认收货/物流延迟/售后换货的风险和路由阈值（007、008、014、056）",
                "动态订单/商品事实的 sourceRef 证据要求",
            ],
            "conclusion": "当前人工标签总体可复用，但存在可定位的边界争议、金额格式差异和动态事实证据不足；不应擅自改写 canonical gold，应在新版本规范中声明投影/归一化并对边界样本追加领域复核。",
        },
        "recommendedRubricChanges": [
            "意图标签单独定义 REFUND_POLICY/REFUND_STATUS/PRODUCT_COMPARE，或在评测前声明投影规则。",
            "金额槽位按数值+币种归一化，同时保留原文 span 诊断。",
            "只要答案声称‘没有/状态/价格/库存/订单’等事实，禁止自动标 NOT_APPLICABLE；增加 UNVERIFIABLE。",
            "动态事实必须绑定 Java/MCP snapshot 或 authoritative negative lookup；通用知识片段不能替代。",
        ],
        "limitations": [
            "这是对标注规范和证据充分性的审计，不是第三个真值来源。",
            "HTTP labels 只适用于 sourceRunId 和 answer SHA-256 完全一致的冻结回放。",
            "样本量 60，不能外推线上客服准确率或 CSAT/FCR。",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    intent = report["intentSlotAudit"]
    answer = report["answerReviewAudit"]
    quality = report["annotationQualityAssessment"]
    lines = [
        "# 客服人工标注审计",
        "",
        "> 本文审计标注过程、规范边界和证据充分性，不改写已封存 final，也不生成新的模型真值。",
        "",
        "## 结果",
        "",
        f"- 意图/槽位人工金标：`{intent['humanVerifiedCaseCount']}` 条；案件级全字段完全一致 `{intent['reviewerAgreement'].get('exactAgreementCaseCount')}/{intent['reviewerAgreement']['caseCount']}`（`{intent['reviewerAgreement']['caseAgreementRate']}`）。",
        f"- 字段级一致性：intent `{intent['reviewerAgreement'].get('fieldStats', {}).get('intent', {}).get('agreementCount')}/{intent['reviewerAgreement']['caseCount']}`，risk `{intent['reviewerAgreement'].get('fieldStats', {}).get('riskLevel', {}).get('agreementCount')}/{intent['reviewerAgreement']['caseCount']}`，slots `{intent['reviewerAgreement'].get('fieldStats', {}).get('slots', {}).get('agreementCount')}/{intent['reviewerAgreement']['caseCount']}`；案件级低一致主要由槽位 schema/格式分歧造成。",
        f"- HTTP 最终答案冻结回放：`{answer['frozenReplayCaseCount']}` 条；案件级完全一致率 `{answer['reviewerAgreement'].get('caseAgreementRate')}`。",
        f"- HTTP 标签中运行时证据不足：`{len(answer.get('unverifiableRuntimeFactCaseIds') or [])}` 条；其中人工仍判回答正确 `{len(answer['unverifiableDespiteCorrectLabel'])}` 条，不能直接当作已事实验证的正确率。",
        f"- 金额槽位存在格式不一致：`{intent['amountNormalizationInconsistency']['caseCount']}` 条；严格字符串 EM 会低估质量。",
        f"- 槽位原文可追溯性：`{intent['slotProvenanceAudit']['provenanceCounts'].get('DIRECT_SPAN', 0)}` 个直接 span，"
        f"`{intent['slotProvenanceAudit']['provenanceCounts'].get('DERIVED_ALLOWED_RULE', 0)}` 个允许派生，"
        f"`{len(intent['slotProvenanceAudit']['unsupportedCaseIds'])}` 个无法由原文解释。",
        f"- 标注质量结论：`{quality['status']}`；不自动推翻仲裁标签，但标记 taxonomy 边界与领域资格证据缺口。",
        "",
        "## 需要回看",
        "",
    ]
    for item in intent["potentialTaxonomyDisputes"]:
        lines.append(f"- `{item['caseId']}`：{item['status']}，{item['reason']}")
    provenance = intent["slotProvenanceAudit"]
    lines.extend(["", "## 槽位标注合理性", ""])
    lines.append(
        f"- 原文可见/可解释槽位：`DIRECT_SPAN={provenance['provenanceCounts'].get('DIRECT_SPAN', 0)}`，"
        f"`CURRENCY_OR_UNIT_NORMALIZED={provenance['provenanceCounts'].get('CURRENCY_OR_UNIT_NORMALIZED', 0)}`，"
        f"`DERIVED_ALLOWED_RULE={provenance['provenanceCounts'].get('DERIVED_ALLOWED_RULE', 0)}`。"
    )
    lines.append(
        f"- 无法从输入解释的槽位案件：`{', '.join(provenance['unsupportedCaseIds']) or '无'}`；"
        "这项检查不是自动改标，若出现应先由领域人员复核。"
    )
    for item in provenance["derivedAllowedCases"]:
        lines.append(
            f"- `{item['caseId']}`：`{item['field']}={item['value']}` 属于 `{item['provenance']}`，"
            "原文含明确数量短语，不能记为幻觉。"
        )
    lines.extend(["", "## 证据不足样本", ""])
    for item in answer["unverifiableDespiteCorrectLabel"]:
        lines.append(f"- `{item['caseId']}`：{item['message']}；答案片段：`{item.get('answerExcerpt', '')}`")
    lines.extend(
        [
            "",
            "## 标注合理性判断",
            "",
            f"- {quality['conclusion']}",
            f"- 明确误标候选：`{', '.join(quality['confirmedMislabelCaseIds']) or '未发现可由原文直接证伪的案例'}`；"
            "边界样本仍需有客服领域经验的第三人复核。",
            f"- 领域复核优先项：{'；'.join(quality['recommendedExpertReview'])}。",
            f"- `{len(answer.get('unverifiableRuntimeFactCaseIds') or [])}` 条 `UNVERIFIABLE_RUNTIME_FACT` 只说明固定回放缺少权威证据，其中 `{len(answer['unverifiableDespiteCorrectLabel'])}` 条被人工判为正确；不可反向当成模型错误或正确，需要新运行绑定 Java/MCP snapshot 后重新审查。",
            "",
            "",
            "## 后续规范",
            "",
            "- 增加 `UNVERIFIABLE` 标签；事实性断言不能自动标 `NOT_APPLICABLE`。",
            "- 金额同时输出原文 span 与数值归一化指标。",
            "- 动态订单、价格、库存、优惠券、物流结论必须绑定 Java/MCP 权威快照或负查询证据。",
            "- HTTP 输出变化后重新双人盲审，不能复用旧答案标签。",
        ]
    )
    return "\n".join(lines) + "\n"


def write_audit(
    *, json_path: Path = DEFAULT_JSON, markdown_path: Path = DEFAULT_MD
) -> dict[str, Any]:
    report = build_annotation_audit()
    atomic_write_json(json_path, report, overwrite=True)
    atomic_write_text(markdown_path, render_markdown(report), overwrite=True)
    return report


if __name__ == "__main__":
    result = write_audit()
    print(json.dumps(result["answerReviewAudit"]["labelEvidenceStatusCounts"], ensure_ascii=False))
