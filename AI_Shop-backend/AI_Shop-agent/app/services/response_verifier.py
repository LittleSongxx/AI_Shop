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
_ORDER_STATUS_FACT_RE = re.compile(
    r"(?:订单|订单状态|履约状态).{0,24}(?:待付款|已付款|待发货|已发货|运输中|已签收|已收货|已完成|已退款|退款中|处理中)"
)
_ORDER_AMOUNT_FACT_RE = re.compile(
    r"(?:订单|订单项|退款|支付|金额|价格).{0,20}(?:金额|支付|退款|应付|实付)?"
    r".{0,12}(?:￥|¥|人民币|\d+(?:\.\d{1,2})?元)"
)
_ORDER_PRODUCT_ASSERTION_RE = re.compile(
    r"(?:商品(?:名称)?|商品名)\s*(?:是|为|：|:)\s*[“\"']?([^。！？!?；;\n]{1,100})|"
    r"(?:买了|购买了|买的是|购买的是|已定位到)\s*[“\"']?([^。！？!?；;\n]{1,100})"
)
_ORDER_PROPERTY_FACT_RE = re.compile(
    r"(?:订单|订单项|商品).{0,20}(?:规格|属性|型号|颜色|尺码|版本).{0,20}(?:是|为|：|:)"
)
_ORDER_PAYMENT_FACT_RE = re.compile(
    r"(?:订单|支付|付款).{0,20}(?:支付方式|支付渠道|支付场景).{0,20}(?:是|为|：|:)"
)
_ORDER_QUANTITY_FACT_RE = re.compile(
    r"(?:订单|订单项|商品).{0,20}(?:数量|买了|购买了).{0,12}(?:件|个|份|\d+)"
)
_POLICY_CLAIM_RE = re.compile(
    r"(?:\d+|七|十五|三十)天(?:内|无理由)|无理由退货|运费由.{0,12}承担|"
    r"政策规定|平台规定|仅限.{0,16}(?:退款|退货|换货)|必须.{0,16}(?:凭证|条件)|"
    r"(?:符合|满足|具备|不符合|不满足).{0,8}(?:退款|退货|换货)(?:条件|资格)?|"
    r"(?:可以|能够|不能|不可).{0,10}(?:退款|退货|换货)"
)
_ACTION_CAPABILITY_PATTERNS: dict[str, re.Pattern[str]] = {
    "CANCEL_ORDER": re.compile(
        r"(?:可以|能够|允许|可|不能|不可|不允许).{0,8}取消(?:订单)?"
    ),
    "CONFIRM_RECEIPT": re.compile(
        r"(?:可以|能够|允许|可|不能|不可|不允许).{0,8}确认收货"
    ),
    "PRODUCT_REVIEW": re.compile(
        r"(?:可以|能够|允许|可|不能|不可|不允许).{0,8}(?:首次)?评价"
    ),
    "RECOMMENT": re.compile(
        r"(?:可以|能够|允许|可|不能|不可|不允许).{0,8}追评"
    ),
}
_NEGATIVE_CAPABILITY_RE = re.compile(r"(?:不能|不可|不允许|不符合|无法)")
_AFTER_SALES_CAPABILITY_RE = re.compile(
    r"(?:可以|能够|可|不能|不可|不符合|符合).{0,10}(?:申请)?(?:退款|退货)|"
    r"(?:退款|退货).{0,8}(?:资格|条件).{0,8}(?:符合|不符合)"
)
_GENERAL_POLICY_RULE_RE = re.compile(
    r"(?:\d+|七|十五|三十)天(?:内|无理由)|政策规定|平台规定|运费由|必须.{0,16}凭证|"
    r"待付款订单.{0,16}取消|进入发货流程.{0,20}取消|已发货.{0,16}售后"
)
_ORDER_ID_IN_TEXT_RE = re.compile(
    r"订单(?:号)?\s*[：:]?\s*[\"'“”]?([A-Za-z0-9][A-Za-z0-9_-]{1,119})",
    re.I,
)
_ORDER_ITEM_ID_IN_TEXT_RE = re.compile(
    r"订单项(?:ID|编号)?\s*[：:]?\s*[\"'“”]?([A-Za-z0-9][A-Za-z0-9_-]{1,119})",
    re.I,
)
_CASE_SPECIFIC_CAPABILITY_RE = re.compile(
    r"(?:该订单|当前订单|本订单|这(?:个|笔|张)?订单|本次(?:资格)?核验|业务系统|订单号|订单项)"
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
    "DYNAMIC_FACT_WITHOUT_CLAIM": (
        "订单事实的字段证据不完整，暂时不能确认这项具体信息。"
        "请稍后重试，或回复“转人工”。"
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
    "ACTION_CAPABILITY_WITHOUT_DECISION": (
        "已核验订单事实，但尚未取得与该订单和操作绑定的资格决定，"
        "因此不能确认当前是否可办理。请稍后重试，或回复“转人工”。"
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
        # A resolver outcome is routing metadata, not evidence.  Only a
        # Java-owned order ref with a self-consistent field claim can support
        # a dynamic order statement.  Historical NO_ELIGIBLE checkpoints may
        # still support their claimed status, but never an eligibility result
        # unless a separate capability decision is present below.
        verified_order_context = (
            order_outcome in {"RESOLVED", "NO_ELIGIBLE"}
            and has_dynamic_order_authority(business_refs)
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

        unsupported_order_fact = _unsupported_order_fact(text, business_refs)
        if unsupported_order_fact:
            issues.append(
                VerificationIssue(
                    "DYNAMIC_FACT_WITHOUT_CLAIM",
                    unsupported_order_fact,
                )
            )

        unsupported_capability = _unsupported_action_capability(text, business_refs)
        unsupported_after_sales = _unsupported_after_sales_capability(
            text, business_refs
        )
        if unsupported_capability:
            issues.append(
                VerificationIssue(
                    "ACTION_CAPABILITY_WITHOUT_DECISION",
                    unsupported_capability,
                )
            )

        unsupported_policy_claim = _has_unsupported_policy_claim(text)
        if (
            unsupported_policy_claim
            and _after_sales_claim_supported(text, business_refs)
            and not _GENERAL_POLICY_RULE_RE.search(text)
        ):
            # A persisted eligibility decision may support only the bounded
            # conclusion for this order/item.  It cannot support general
            # rules such as return windows, freight allocation, or evidence
            # requirements; those still require published RAG evidence.
            unsupported_policy_claim = False
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
        if unsupported_after_sales:
            issues.append(
                VerificationIssue(
                    "ACTION_CAPABILITY_WITHOUT_DECISION",
                    unsupported_after_sales,
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


def _business_ref_rows(source_refs: list[dict] | dict | None) -> list[dict]:
    """Normalize an explicitly supplied business channel for helper checks.

    ``verify`` receives the v3 envelope, while runtime helpers sometimes
    receive the already-separated ``tool_source_refs`` list.  A bare list is
    therefore accepted only by these internal business-authority helpers; it
    is never treated as RAG evidence by the verifier.
    """

    if isinstance(source_refs, list):
        return [item for item in source_refs if isinstance(item, dict)]
    return _business_sources(source_refs)


def _trusted_order_ref(ref: dict[str, Any]) -> bool:
    if (
        str(ref.get("type") or "").lower() != "order"
        or str(ref.get("source") or "") != "JAVA_ORDER_SERVICE"
        or ref.get("matched", True) is False
    ):
        return False
    order_id = str(ref.get("orderId") or ref.get("id") or "").strip()
    if not order_id:
        return False
    return any(
        isinstance(claim, dict)
        and claim.get("claimType") == "DYNAMIC_FACT"
        and claim.get("sourceType") == "JAVA_ORDER_SERVICE"
        and claim.get("sourceId") == order_id
        and claim.get("subjectType") == "order"
        and claim.get("subjectId") == order_id
        and claim.get("factPath") == "order.orderId"
        and str(claim.get("value") or "") == order_id
        for claim in ref.get("claims") or []
    )


def _trusted_order_refs_for_text(
    source_refs: list[dict] | dict | None, text: str
) -> list[dict[str, Any]]:
    """Return authenticated order snapshots relevant to an answer.

    A valid order id claim establishes object identity only.  The caller still
    has to check the specific field claim before presenting a status, amount,
    item or payment fact.
    """

    order_ids = _text_order_ids(text)
    refs = [
        ref
        for ref in _business_ref_rows(source_refs)
        if _trusted_order_ref(ref)
    ]
    if order_ids:
        return [
            ref
            for ref in refs
            if str(ref.get("orderId") or ref.get("id") or "").strip()
            in order_ids
        ]
    # A deterministic order-reference response may omit the id in a short
    # sentence, but only one authenticated snapshot may then be in scope.
    return refs if len(refs) == 1 else []


def _order_claims(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        claim
        for ref in refs
        for claim in ref.get("claims") or []
        if isinstance(claim, dict)
        and claim.get("claimType") == "DYNAMIC_FACT"
        and claim.get("sourceType") == "JAVA_ORDER_SERVICE"
        and claim.get("sourceId")
        == str(ref.get("orderId") or ref.get("id") or "")
    ]


def _mentioned_order_product(text: str) -> str | None:
    match = _ORDER_PRODUCT_ASSERTION_RE.search(text or "")
    if not match:
        return None
    value = next((group for group in match.groups() if group), "")
    # Keep order ids and trailing explanation out of the asserted product span.
    value = re.split(r"[（(]?(?:订单|订单号)\s*[：:]?\s*[A-Za-z0-9_-]+", value)[0]
    return value.strip(" 　“”\"'：:，,。") or None


def _has_claim_path(
    claims: list[dict[str, Any]], paths: set[str]
) -> bool:
    return any(claim.get("factPath") in paths for claim in claims)


def _unsupported_order_fact(
    text: str, source_refs: list[dict] | dict | None
) -> str | None:
    """Reject an order answer whose concrete field lacks a Java claim.

    This closes the gap between “the response has a valid order id” and “the
    response is allowed to invent the product, status or payment details of
    that order”.  Generic policy prose without a concrete order id remains on
    the RAG policy path.
    """

    refs = _trusted_order_refs_for_text(source_refs, text)
    if not refs:
        return None
    claims = _order_claims(refs)
    if _ORDER_STATUS_FACT_RE.search(text) and not _has_claim_path(
        claims, {"order.orderStatus", "order.orderStatusName"}
    ):
        return "回答中的订单状态没有对应的 Java 动态字段 claim"
    if _ORDER_AMOUNT_FACT_RE.search(text) and not _has_claim_path(
        claims, {"order.amount", "order_item.itemAmount"}
    ):
        return "回答中的订单金额没有对应的 Java 动态字段 claim"
    product = _mentioned_order_product(text)
    if product:
        product_claims = [
            claim
            for claim in claims
            if claim.get("factPath") == "order_item.productName"
            and str(claim.get("value") or "").strip()
        ]
        if not product_claims:
            return "回答中的商品名没有对应的订单项动态字段 claim"
        normalized_product = re.sub(r"\s+", "", product).lower()
        if not any(
            normalized_product in re.sub(r"\s+", "", str(claim.get("value") or "")).lower()
            or re.sub(r"\s+", "", str(claim.get("value") or "")).lower()
            in normalized_product
            for claim in product_claims
        ):
            return "回答中的商品名与订单项动态字段 claim 不一致"
    if _ORDER_PROPERTY_FACT_RE.search(text) and not _has_claim_path(
        claims, {"order_item.propertyInfo", "order_item.productName"}
    ):
        return "回答中的商品规格没有对应的订单项动态字段 claim"
    if _ORDER_PAYMENT_FACT_RE.search(text) and not _has_claim_path(
        claims, {"order.payScene", "order.payChannel"}
    ):
        return "回答中的支付信息没有对应的 Java 动态字段 claim"
    if _ORDER_QUANTITY_FACT_RE.search(text) and not _has_claim_path(
        claims, {"order_item.buyCount"}
    ):
        return "回答中的商品数量没有对应的订单项动态字段 claim"
    return None


def has_dynamic_order_authority(source_refs: list[dict] | dict | None) -> bool:
    """Whether the business channel contains a self-consistent order claim."""

    return any(_trusted_order_ref(ref) for ref in _business_ref_rows(source_refs))


def _trusted_action_decisions(
    source_refs: list[dict] | dict | None,
) -> list[dict[str, str | None]]:
    decisions: list[dict[str, str | None]] = []
    for ref in _business_ref_rows(source_refs):
        if (
            str(ref.get("type") or "").lower() != "action_capability"
            or str(ref.get("source") or "") != "JAVA_ORDER_SERVICE"
        ):
            continue
        action = str(ref.get("action") or "").strip().upper()
        decision = str(ref.get("decision") or "").strip().upper()
        order_id = str(ref.get("orderId") or "").strip()
        item_id = str(ref.get("orderItemId") or "").strip() or None
        if (
            not action
            or not order_id
            or not str(ref.get("capabilityVersion") or "").strip()
            or not str(ref.get("evaluatedAt") or "").strip()
            or decision not in {
            "ALLOWED",
            "DENIED",
            "MANUAL_REVIEW",
            "UNAVAILABLE",
            }
        ):
            continue
        valid_claim = any(
            isinstance(claim, dict)
            and claim.get("claimType") == "ACTION_CAPABILITY_DECISION"
            and claim.get("sourceType") == "JAVA_ORDER_SERVICE"
            and claim.get("sourceId") == order_id
            and claim.get("subjectId") == order_id
            and claim.get("action") == action
            and claim.get("decision") == decision
            and (
                item_id is None
                or str(claim.get("orderItemId") or "") == item_id
            )
            for claim in ref.get("claims") or []
        )
        if valid_claim:
            decisions.append(
                {
                    "action": action,
                    "decision": decision,
                    "orderId": order_id,
                    "orderItemId": item_id,
                }
            )
    return decisions


def _trusted_after_sales_decisions(
    source_refs: list[dict] | dict | None,
) -> list[dict[str, str | None]]:
    decisions: list[dict[str, str | None]] = []
    for ref in _business_ref_rows(source_refs):
        if (
            str(ref.get("type") or "").lower() != "after_sales_eligibility"
            or str(ref.get("source") or "")
            != "AGENT_AFTER_SALES_POLICY_ENGINE"
        ):
            continue
        decision_id = str(ref.get("decisionId") or "").strip()
        action = str(ref.get("action") or "").strip().upper()
        decision = str(ref.get("decision") or "").strip().upper()
        order_id = str(ref.get("orderId") or "").strip()
        item_id = str(ref.get("orderItemId") or "").strip() or None
        if not decision_id or not action or not order_id or decision not in {
            "ELIGIBLE",
            "INELIGIBLE",
            "NEEDS_EVIDENCE",
            "POLICY_UNAVAILABLE",
            "CONFLICT",
        }:
            continue
        valid_claim = any(
            isinstance(claim, dict)
            and claim.get("claimType") == "AFTER_SALES_ELIGIBILITY_DECISION"
            and claim.get("sourceType")
            == "AGENT_AFTER_SALES_POLICY_ENGINE"
            and claim.get("sourceId") == decision_id
            and claim.get("subjectId") == order_id
            and claim.get("decisionId") == decision_id
            and claim.get("action") == action
            and claim.get("decision") == decision
            and (
                item_id is None
                or str(claim.get("orderItemId") or "") == item_id
            )
            for claim in ref.get("claims") or []
        )
        if valid_claim:
            decisions.append(
                {
                    "decisionId": decision_id,
                    "action": action,
                    "decision": decision,
                    "orderId": order_id,
                    "orderItemId": item_id,
                }
            )
    return decisions


def _text_order_ids(text: str) -> set[str]:
    ids = {value.strip() for value in _ORDER_ID_IN_TEXT_RE.findall(text or "")}
    # Support the common compact form ``SM2026...`` even when the model omits
    # the Chinese label.  This is intentionally narrower than a generic token
    # extractor so ordinary prose cannot select a business reference.
    ids.update(
        value.strip()
        for value in re.findall(
            r"\b(?:SM|SO|ORD|ORDER)[-_A-Z0-9]{2,119}\b", text or "", re.I
        )
    )
    return {value for value in ids if value}


def _text_order_item_ids(text: str) -> set[str]:
    return {value.strip() for value in _ORDER_ITEM_ID_IN_TEXT_RE.findall(text or "") if value.strip()}


def _claim_clause(text: str, start: int, end: int) -> str:
    left = max(
        (text.rfind(separator, 0, start) for separator in "。！？!?；;，,\n"),
        default=-1,
    )
    right_candidates = [
        position
        for separator in "。！？!?；;，,\n"
        if (position := text.find(separator, end)) >= 0
    ]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1 : right].strip()


def _sentence_span(text: str, start: int, end: int) -> str:
    """Return the full sentence around a claim, retaining adversative clauses."""

    separators = "。！？!?；;\n"
    left = max((text.rfind(separator, 0, start) for separator in separators), default=-1)
    right_candidates = [
        position
        for separator in separators
        if (position := text.find(separator, end)) >= 0
    ]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1 : right].strip()


def _capability_case_specific(clause: str, decisions: list[dict]) -> bool:
    return bool(
        _CASE_SPECIFIC_CAPABILITY_RE.search(clause or "")
        or _text_order_ids(clause)
        or _text_order_item_ids(clause)
        or (decisions and not _GENERAL_POLICY_RULE_RE.search(clause or ""))
    )


def _decision_matches_target(
    candidate: dict,
    *,
    action: str,
    expected_decision: str,
    order_ids: set[str],
    item_ids: set[str],
) -> bool:
    if candidate.get("action") != action or candidate.get("decision") != expected_decision:
        return False
    if order_ids and str(candidate.get("orderId") or "") not in order_ids:
        return False
    candidate_item = str(candidate.get("orderItemId") or "")
    if item_ids and candidate_item not in item_ids:
        return False
    return True


def _capability_expected_decision(match_text: str) -> str:
    if _NEGATIVE_CAPABILITY_RE.search(match_text):
        return "DENIED"
    if re.search(r"需要人工复核|转人工复核", match_text):
        return "MANUAL_REVIEW"
    if re.search(r"暂时无法取得|无法给出.*结论|资格服务.*不可用", match_text):
        return "UNAVAILABLE"
    return "ALLOWED"


def _unsupported_action_capability(
    text: str, source_refs: list[dict] | dict | None
) -> str | None:
    decisions = _trusted_action_decisions(source_refs)
    order_ids = _text_order_ids(text)
    item_ids = _text_order_item_ids(text)
    for action, pattern in _ACTION_CAPABILITY_PATTERNS.items():
        match = pattern.search(text or "")
        if not match:
            continue
        clause = _claim_clause(text, match.start(), match.end())
        if not _capability_case_specific(clause, decisions):
            continue
        expected = _capability_expected_decision(match.group(0))
        if any(
            _decision_matches_target(
                candidate,
                action=action,
                expected_decision=expected,
                order_ids=order_ids,
                item_ids=item_ids,
            )
            for candidate in decisions
        ):
            continue
        if not decisions:
            return (
                f"回答声称订单具备“{action}”资格，但缺少 Java 业务系统返回的匹配资格决定"
            )
        return (
            f"回答中的“{action}/{expected}”与已核验的订单、订单项或资格决定不匹配"
        )
    return None


def _after_sales_expected_decision(match_text: str) -> str | None:
    if _NEGATIVE_CAPABILITY_RE.search(match_text) or re.search(
        r"不符合|不满足", match_text
    ):
        return "INELIGIBLE"
    if re.search(r"需要补充|缺少.*凭证|需要证据", match_text):
        return "NEEDS_EVIDENCE"
    if re.search(r"无法取得|暂时无法|政策服务.*不可用", match_text):
        return "POLICY_UNAVAILABLE"
    return "ELIGIBLE"


def _unsupported_after_sales_capability(
    text: str, source_refs: list[dict] | dict | None
) -> str | None:
    decisions = _trusted_after_sales_decisions(source_refs)
    match = _AFTER_SALES_CAPABILITY_RE.search(text or "")
    if not match:
        return None
    clause = _claim_clause(text, match.start(), match.end())
    sentence = _sentence_span(text, match.start(), match.end())
    local_start = max(0, clause.find(match.group(0)))
    if _POLICY_ABSTENTION_RE.search(sentence) or (
        _POLICY_UNCERTAINTY_PREFIX_RE.search(clause[:local_start])
        and not _POLICY_ADVERSATIVE_RE.search(clause[:local_start])
    ):
        return None
    # A sentence such as “平台规定七天内可退货” is a published-policy
    # statement, not a per-order eligibility result.  Keep it on the RAG gate.
    if not _capability_case_specific(clause, decisions):
        return None
    expected = _after_sales_expected_decision(match.group(0) if match else text)
    if expected is None:
        return None
    order_ids = _text_order_ids(text)
    item_ids = _text_order_item_ids(text)
    for candidate in decisions:
        if candidate.get("decision") != expected:
            continue
        if order_ids and str(candidate.get("orderId") or "") not in order_ids:
            continue
        if item_ids and str(candidate.get("orderItemId") or "") not in item_ids:
            continue
        return None
    if not decisions:
        return "回答声称该订单具备售后资格，但缺少策略引擎返回的匹配资格决定"
    return "回答中的售后资格结论与已核验的订单、订单项或策略决定不匹配"


def _after_sales_claim_supported(
    text: str, source_refs: list[dict] | dict | None
) -> bool:
    return _unsupported_after_sales_capability(text, source_refs) is None and bool(
        _trusted_after_sales_decisions(source_refs)
    )


def has_trusted_capability_decision(
    source_refs: list[dict] | dict | None,
) -> bool:
    """Whether a business channel contains a validated capability decision."""

    return bool(
        _trusted_action_decisions(source_refs)
        or _trusted_after_sales_decisions(source_refs)
    )


def requires_published_policy_evidence(
    text: str, source_refs: list[dict] | dict | None = None
) -> bool:
    """Return whether prose contains a general rule needing published RAG."""

    if _GENERAL_POLICY_RULE_RE.search(text or ""):
        return True
    unsupported = _has_unsupported_policy_claim(text or "")
    if unsupported and _after_sales_claim_supported(text or "", source_refs):
        return False
    return unsupported


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
    excluded_brands = {
        str(item).strip().lower()
        for item in constraints.get("excludedBrands") or []
        if str(item).strip()
    }
    excluded_terms = {
        str(item).strip().lower()
        for item in constraints.get("excludedTerms") or []
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
        searchable = " ".join(
            str(candidate.get(key) or "").lower()
            for key in (
                "name",
                "productName",
                "brand",
                "description",
                "features",
                "category",
                "reason",
            )
        )
        if any(term in searchable for term in excluded_brands):
            return False
        if any(term in searchable for term in excluded_terms):
            return False
        if required_brands and not any(term in searchable for term in required_brands):
            return False
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
