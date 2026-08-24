from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.rag.prompt_builder import RAG_REFUSAL_TEXT, uncited_grounded_sentences

_DYNAMIC_BIZ_TOOLS = {
    "query_order": frozenset({"QUERY_ORDERS"}),
    "query_logistics": frozenset({"QUERY_LOGISTICS"}),
    "query_comment": frozenset({"QUERY_COMMENT"}),
    "query_coupon": frozenset({"QUERY_USER_COUPONS"}),
    "product_search": frozenset({"SEARCH_PRODUCTS", "GET_PRODUCT_DETAIL"}),
    "support_case_list": frozenset({"QUERY_SUPPORT_CASES"}),
    "support_case_detail": frozenset({"QUERY_SUPPORT_CASES"}),
}
_DYNAMIC_FACT_RE = re.compile(
    r"(?:订单|物流|退款|优惠券|库存|价格|工单).{0,20}"
    r"(?:待付款|已付款|已发货|已签收|已完成|处理中|成功|失败|剩余|￥|¥|\d+\.\d{2})"
)
_POLICY_CLAIM_RE = re.compile(
    r"(?:\d+|七|十五|三十)天(?:内|无理由)|无理由退货|运费由.{0,12}承担|"
    r"政策规定|平台规定|仅限.{0,16}(?:退款|退货|换货)|必须.{0,16}(?:凭证|条件)|"
    r"(?:符合|满足|具备|不符合|不满足).{0,8}(?:退款|退货|换货)(?:条件|资格)?|"
    r"(?:可以|能够|不能|不可).{0,10}(?:退款|退货|换货)"
)
_POLICY_ABSTENTION_RE = re.compile(
    r"(?:未找到|没有|缺少).{0,20}(?:政策|规则|依据|证据)|"
    r"(?:无法|不能|暂不能).{0,12}(?:确认|判断|核实).{0,16}(?:政策|条件|资格|是否)|"
    r"(?:政策|条件|资格).{0,16}(?:无法|不能|暂不能).{0,12}(?:确认|判断|核实)|"
    r"(?:不能|不会|不).{0,12}(?:给出|作出).{0,12}(?:确定|明确).{0,8}(?:政策|资格|结论)"
)
_POLICY_CLAUSE_RE = re.compile(r"[^。！？!?；;，,\n]+")
_POLICY_UNCERTAINTY_PREFIX_RE = re.compile(
    r"(?:无法|暂时无法|暂无法|不能|暂不能).{0,12}(?:确认|判断|核实)"
)
_POLICY_ADVERSATIVE_RE = re.compile(r"(?:但|但是|不过|然而|可是|仍然|最终|其实)")
_RAG_CITATION_RE = re.compile(r"\[(\d+)]")
_CURRENT_RAG_ABSTENTION_RE = re.compile(
    r"^(?:根据当前知识库|当前(?:没有|未检索到|缺少)|本轮(?:没有|未检索到)).{0,30}"
    r"(?:无法确认|不能确认|没有足够|缺少依据|联系人工)"
)
_GENERIC_POLICY_STATUS_RE = re.compile(
    r"(?:待付款订单|已发货通常|进入发货流程|取决于当前履约状态|"
    r"售后申请应从本人订单详情|退款申请应根据订单详情)"
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
    "UNNECESSARY_RAG_ABSTENTION": (
        "已检索到相关规则，但本次回答未能正确使用证据。请稍后重试，或回复“转人工”。"
    ),
    "INVALID_RAG_CITATION": (
        "本次回答的知识引用不完整或无效。请稍后重试，或回复“转人工”。"
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
    # ``passed`` describes the original model draft.  A separately verified
    # fallback is intentionally exposed as a different signal so dashboards
    # cannot turn a repaired answer into a misleading model-pass rate.
    fallback_verified: bool = False
    terminal_quality: str = "UNVERIFIED"

    def quality(self) -> dict[str, Any]:
        return {
            "verifierPassed": self.passed,
            "verifierAction": self.action,
            "verifierIssues": [issue.public() for issue in self.issues],
            "fallbackVerified": self.fallback_verified,
            "terminalQuality": self.terminal_quality,
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
        rag_citation_required: bool = False,
        rag_evidence_state: str | None = None,
        rag_source_refs: list[dict] | None = None,
        safe_fallback: str | None = None,
    ) -> VerificationResult:
        text = str(assistant or "").strip()
        called = frozenset(str(tool) for tool in tools_called or [])
        issues: list[VerificationIssue] = []
        # New callers pass an explicit RAG channel.  The legacy ``source_refs``
        # argument remains supported for old replay fixtures, but a business
        # snapshot in ``businessSources`` must never satisfy a policy gate.
        effective_rag_refs = (
            rag_source_refs
            if rag_source_refs is not None
            else _legacy_rag_sources(source_refs)
        )
        source_count = _source_count(effective_rag_refs)
        verified_action_card = (
            str(biz_type or "") == "action_confirm" and has_pending_action
        )

        if (
            str(rag_evidence_state or "").upper() == "SUPPORTED"
            and source_count > 0
            and (
                text == RAG_REFUSAL_TEXT
                or bool(_CURRENT_RAG_ABSTENTION_RE.search(text))
            )
        ):
            issues.append(
                VerificationIssue(
                    "UNNECESSARY_RAG_ABSTENTION",
                    "检索证据充分，但回答把当前轮误判为证据不足",
                )
            )

        # ACTION_CONFIRM is a server-built business card whose authority is the
        # durable pending row and verified tool call, not prose citation syntax.
        if (
            rag_citation_required
            and not verified_action_card
            and source_count > 0
            and text != RAG_REFUSAL_TEXT
        ):
            citations = [int(value) for value in _RAG_CITATION_RE.findall(text)]
            invalid = sorted({value for value in citations if value < 1 or value > source_count})
            uncited = uncited_grounded_sentences(text)
            if not citations or invalid or uncited:
                detail = "回答使用了 RAG 事实，但没有提供有效编号引用"
                if invalid:
                    detail = f"回答引用越界：{invalid}，可用编号为 1..{source_count}"
                elif uncited:
                    detail = f"存在未就近引用的事实句：{len(uncited)} 条"
                issues.append(
                    VerificationIssue(
                        "INVALID_RAG_CITATION",
                        detail,
                    )
                )

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
        order_outcome = str(order_resolution or "").upper()
        business_refs = _business_sources(source_refs)
        # NO_ELIGIBLE is a verified read result, not an executable target. It
        # may support a status-based refusal, but never authorizes a write.
        verified_order_context = order_outcome == "RESOLVED" or (
            order_outcome == "NO_ELIGIBLE"
            and bool(business_refs)
            and any(ref.get("matched", True) is not False for ref in business_refs)
        )
        if required and not called.intersection(required) and not verified_order_context:
            issues.append(
                VerificationIssue(
                    "DYNAMIC_FACT_WITHOUT_TOOL",
                    f"{biz_type} 缺少所需业务工具依据",
                )
            )
        elif (
            _DYNAMIC_FACT_RE.search(text)
            and not called.intersection(set().union(*_DYNAMIC_BIZ_TOOLS.values()))
            and not verified_order_context
            and not (
                # A cited, generic policy sentence may mention lifecycle
                # states (for example "待付款/已发货") without claiming the
                # user's actual order is in that state.  Keep the runtime
                # tool requirement for order-specific text and identifiers.
                rag_citation_required
                and source_count > 0
                and _GENERIC_POLICY_STATUS_RE.search(text)
                and not re.search(r"\b(?:SM|SO|ORD|ORDER)[-_A-Z0-9]{4,}\b", text, re.I)
            )
        ):
            issues.append(
                VerificationIssue(
                    "DYNAMIC_FACT_WITHOUT_TOOL",
                    "回答包含动态业务状态，但没有工具或订单解析依据",
                )
            )

        unsupported_policy_claim = _has_unsupported_policy_claim(text)
        policy_abstained = bool(_POLICY_ABSTENTION_RE.search(text))
        if not _has_sources(effective_rag_refs) and (
            unsupported_policy_claim or (policy_evidence_required and not policy_abstained)
        ):
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
            return VerificationResult(
                True,
                "PASS",
                text,
                (),
                fallback_verified=False,
                terminal_quality="PASS",
            )
        primary = max(issues, key=_issue_priority)
        action = "HANDOFF" if primary.severity == "CRITICAL" else "DEGRADE"
        if primary.code == "RECOMMENDATION_CONSTRAINT_VIOLATION":
            action = "CLARIFY"
        fallback_assistant = _FALLBACKS[primary.code]
        candidate = str(safe_fallback or "").strip()
        fallback_verified = False
        if candidate:
            fallback_check = self.verify(
                assistant=candidate,
                biz_type=biz_type,
                tools_called=tools_called,
                source_refs=source_refs,
                has_pending_action=has_pending_action,
                order_resolution=order_resolution,
                recommendation_constraints=recommendation_constraints,
                recommendation_candidates=recommendation_candidates,
                support_case=support_case,
                policy_evidence_required=policy_evidence_required,
                rag_citation_required=rag_citation_required,
                rag_evidence_state=rag_evidence_state,
                rag_source_refs=rag_source_refs,
            )
            if fallback_check.passed:
                fallback_assistant = fallback_check.assistant
                fallback_verified = True
        terminal_quality = (
            "SAFE_DEGRADED"
            if fallback_verified
            else "HANDOFF_REQUIRED"
            if action == "HANDOFF"
            else "DEGRADED_UNVERIFIED"
        )
        return VerificationResult(
            False,
            action,
            fallback_assistant,
            tuple(issues),
            fallback_verified=fallback_verified,
            terminal_quality=terminal_quality,
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


def _legacy_rag_sources(source_refs: list[dict] | dict | None) -> list[dict]:
    """Read the pre-v3 shape without silently reclassifying new channels."""

    if isinstance(source_refs, list):
        return [item for item in source_refs if isinstance(item, dict)]
    if isinstance(source_refs, dict):
        if isinstance(source_refs.get("ragSources"), list):
            return [item for item in source_refs["ragSources"] if isinstance(item, dict)]
        sources = source_refs.get("sources")
        if isinstance(sources, list):
            return [item for item in sources if isinstance(item, dict)]
    return []


def _business_sources(source_refs: list[dict] | dict | None) -> list[dict]:
    """Read only the explicit Java/MCP evidence channel."""

    if isinstance(source_refs, dict):
        return [
            item
            for item in source_refs.get("businessSources") or []
            if isinstance(item, dict)
        ]
    return []


def _source_count(source_refs: list[dict] | dict | None) -> int:
    if isinstance(source_refs, list):
        return sum(isinstance(item, dict) and bool(item) for item in source_refs)
    if isinstance(source_refs, dict):
        sources = source_refs.get("sources")
        if isinstance(sources, list):
            return sum(isinstance(item, dict) and bool(item) for item in sources)
    return 0


def _has_unsupported_policy_claim(text: str) -> bool:
    """Reject deterministic claims even when another clause contains an abstention."""

    for clause_match in _POLICY_CLAUSE_RE.finditer(text):
        clause = clause_match.group(0)
        abstentions = tuple(_POLICY_ABSTENTION_RE.finditer(clause))
        for claim in _POLICY_CLAIM_RE.finditer(clause):
            if any(
                claim.start() < abstention.end() and claim.end() > abstention.start()
                for abstention in abstentions
            ):
                continue
            prefix = clause[: claim.start()]
            uncertainty = tuple(_POLICY_UNCERTAINTY_PREFIX_RE.finditer(prefix))
            if uncertainty:
                scope_tail = prefix[uncertainty[-1].end() :]
                if len(scope_tail) <= 16 and not _POLICY_ADVERSATIVE_RE.search(scope_tail):
                    continue
            return True
    return False


def _recommendations_satisfy(constraints: dict, candidates: list[dict]) -> bool:
    budget_min = constraints.get("budgetMin")
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
    required_brands = {
        str(item).strip().lower()
        for item in constraints.get("requiredBrands") or []
        if str(item).strip()
    }
    for candidate in candidates:
        try:
            minimum = candidate.get("minPrice")
            if minimum is None:
                minimum = candidate.get("price")
            maximum = candidate.get("maxPrice")
            if maximum is None:
                maximum = minimum
            if budget_max is not None and float(minimum) > float(budget_max):
                return False
            if budget_min is not None and float(maximum) < float(budget_min):
                return False
        except (TypeError, ValueError):
            return False
        brand = str(candidate.get("brand") or "").strip().lower()
        if brand and brand in excluded:
            return False
        if required_brands and brand not in required_brands:
            return False
        searchable = " ".join(
            str(candidate.get(key) or "").lower()
            for key in ("name", "brand", "features", "description")
        )
        if required and not required.issubset({term for term in required if term in searchable}):
            return False
    return True


def _valid_support_case(case: dict) -> bool:
    categories = {
        "DAMAGED",
        "WRONG_ITEM",
        "MISSING_ITEM",
        "LOGISTICS",
        "REFUND_DISPUTE",
        "PAYMENT_DISPUTE",
        "ADDRESS_CHANGE",
        "INVOICE",
        "COMPLAINT",
        "OTHER",
    }
    if str(case.get("category") or "").strip().upper() not in categories:
        return False
    description = str(case.get("description") or "").strip()
    if len(description) < 2 or case.get("ownedOrderValidated") is not True:
        return False
    evidence = case.get("evidence")
    if evidence is not None:
        return bool(
            isinstance(evidence, dict)
            and evidence.get("moderationStatus") == "APPROVED"
            and evidence.get("moderationId")
            and str(evidence.get("path") or "").strip()
        )
    return True


def _issue_priority(issue: VerificationIssue) -> int:
    return {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}.get(
        issue.severity, 3
    )


response_verifier = ResponseVerifier()
