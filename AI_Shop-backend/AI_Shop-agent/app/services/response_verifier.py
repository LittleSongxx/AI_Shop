from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_DYNAMIC_BIZ_TOOLS = {
    "query_order": frozenset({"QUERY_ORDERS"}),
    "query_logistics": frozenset({"QUERY_LOGISTICS"}),
    "query_comment": frozenset({"QUERY_COMMENT"}),
    "query_coupon": frozenset({"QUERY_USER_COUPONS"}),
    "product_search": frozenset({"SEARCH_PRODUCTS", "GET_PRODUCT_DETAIL"}),
}
_DYNAMIC_FACT_RE = re.compile(
    r"(?:订单|物流|退款|优惠券|库存|价格).{0,20}"
    r"(?:待付款|已付款|已发货|已签收|已完成|处理中|成功|失败|剩余|￥|¥|\d+\.\d{2})"
)
_POLICY_CLAIM_RE = re.compile(
    r"(?:\d+|七|十五|三十)天(?:内|无理由)|无理由退货|运费由.{0,12}承担|"
    r"政策规定|平台规定|仅限.{0,16}(?:退款|退货|换货)|必须.{0,16}(?:凭证|条件)"
)
_FALLBACKS = {
    "WRITE_WITHOUT_PENDING_ACTION": (
        "未能生成可执行的确认卡片。请核对订单信息后重试，或回复“转人工”。"
    ),
    "DYNAMIC_FACT_WITHOUT_TOOL": (
        "暂时无法从业务系统核实这项实时信息。请稍后重试，或回复“转人工”。"
    ),
    "POLICY_WITHOUT_CITATION": (
        "当前没有检索到足够的已发布规则依据，我不能给出确定的政策结论。"
        "请补充具体商品与订单状态，或回复“转人工”。"
    ),
    "RECOMMENDATION_CONSTRAINT_VIOLATION": (
        "当前候选没有完整满足你的硬性条件。请确认最重要的一项要求，我再重新筛选。"
    ),
    "INVALID_SUPPORT_CASE": (
        "工单信息尚不完整，暂不能提交。请补充订单与问题描述，或回复“转人工”。"
    ),
}


@dataclass(frozen=True)
class VerificationIssue:
    code: str
    detail: str
    severity: str = "HIGH"

    def public(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail, "severity": self.severity}


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    action: str
    assistant: str
    issues: tuple[VerificationIssue, ...]

    def quality(self) -> dict[str, Any]:
        return {
            "verifierPassed": self.passed,
            "verifierAction": self.action,
            "verifierIssues": [issue.public() for issue in self.issues],
        }


class ResponseVerifier:
    def verify(
        self,
        *,
        assistant: str,
        biz_type: str | None,
        tools_called: list[str] | None,
        source_refs: list[dict] | dict | None,
        has_pending_action: bool,
        order_resolution: str | None = None,
        recommendation_constraints: dict | None = None,
        recommendation_candidates: list[dict] | None = None,
        support_case: dict | None = None,
        policy_evidence_required: bool = False,
    ) -> VerificationResult:
        text = str(assistant or "").strip()
        called = frozenset(str(tool) for tool in tools_called or [])
        issues: list[VerificationIssue] = []

        presents_write = str(biz_type or "") == "action_confirm" or bool(
            re.search(r"(?:【)?act_[a-f0-9]{32}(?:】)?", text, re.I)
        )
        if (
            any(tool.startswith("PROPOSE_") for tool in called)
            and presents_write
            and not has_pending_action
        ):
            issues.append(
                VerificationIssue(
                    "WRITE_WITHOUT_PENDING_ACTION",
                    "写工具被调用，但没有服务端可验证的 pending action",
                    "CRITICAL",
                )
            )

        required = _DYNAMIC_BIZ_TOOLS.get(str(biz_type or ""))
        resolved_order = str(order_resolution or "").upper() == "RESOLVED"
        if required and not called.intersection(required) and not resolved_order:
            issues.append(
                VerificationIssue(
                    "DYNAMIC_FACT_WITHOUT_TOOL",
                    f"{biz_type} 缺少所需业务工具依据",
                )
            )
        elif (
            _DYNAMIC_FACT_RE.search(text)
            and not called.intersection(set().union(*_DYNAMIC_BIZ_TOOLS.values()))
            and not resolved_order
        ):
            issues.append(
                VerificationIssue(
                    "DYNAMIC_FACT_WITHOUT_TOOL",
                    "回答包含动态业务状态，但没有工具或订单解析依据",
                )
            )

        if (
            policy_evidence_required or _POLICY_CLAIM_RE.search(text)
        ) and not _has_sources(source_refs):
            issues.append(
                VerificationIssue(
                    "POLICY_WITHOUT_CITATION",
                    "确定性政策结论缺少已发布知识引用",
                )
            )

        if recommendation_constraints and recommendation_candidates:
            if not _recommendations_satisfy(
                recommendation_constraints, recommendation_candidates
            ):
                issues.append(
                    VerificationIssue(
                        "RECOMMENDATION_CONSTRAINT_VIOLATION",
                        "至少一个候选违反预算、必需特征或排除品牌",
                    )
                )

        if support_case is not None and not _valid_support_case(support_case):
            issues.append(
                VerificationIssue(
                    "INVALID_SUPPORT_CASE",
                    "工单缺少类别、描述或经认证的订单归属",
                    "CRITICAL",
                )
            )

        if not issues:
            return VerificationResult(True, "PASS", text, ())
        primary = max(issues, key=_issue_priority)
        action = "HANDOFF" if primary.severity == "CRITICAL" else "DEGRADE"
        if primary.code == "RECOMMENDATION_CONSTRAINT_VIOLATION":
            action = "CLARIFY"
        return VerificationResult(
            False,
            action,
            _FALLBACKS[primary.code],
            tuple(issues),
        )


def _has_sources(source_refs: list[dict] | dict | None) -> bool:
    if isinstance(source_refs, list):
        return any(isinstance(item, dict) and item for item in source_refs)
    if isinstance(source_refs, dict):
        sources = source_refs.get("sources")
        return isinstance(sources, list) and any(
            isinstance(item, dict) and item for item in sources
        )
    return False


def _recommendations_satisfy(constraints: dict, candidates: list[dict]) -> bool:
    budget_max = constraints.get("budgetMax")
    required = {
        str(item).strip().lower()
        for item in constraints.get("requiredTerms") or []
        if str(item).strip()
    }
    excluded = {
        str(item).strip().lower()
        for item in constraints.get("excludedBrands") or []
        if str(item).strip()
    }
    for candidate in candidates:
        try:
            if budget_max is not None and float(candidate.get("price")) > float(budget_max):
                return False
        except (TypeError, ValueError):
            return False
        brand = str(candidate.get("brand") or "").strip().lower()
        if brand and brand in excluded:
            return False
        searchable = " ".join(
            str(candidate.get(key) or "").lower()
            for key in ("name", "brand", "features", "description")
        )
        if required and not required.issubset({term for term in required if term in searchable}):
            return False
    return True


def _valid_support_case(case: dict) -> bool:
    return bool(
        str(case.get("category") or "").strip()
        and str(case.get("description") or "").strip()
        and case.get("ownedOrderValidated") is True
    )


def _issue_priority(issue: VerificationIssue) -> int:
    return {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}.get(
        issue.severity, 3
    )


response_verifier = ResponseVerifier()
