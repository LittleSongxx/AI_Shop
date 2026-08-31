from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.constants import ORDER_STATUS_NAMES
from app.rag.prompt_builder import (
    RAG_REFUSAL_TEXT,
    is_current_rag_abstention,
    uncited_grounded_sentences,
)

_DYNAMIC_BIZ_TOOLS = {
    "query_order": frozenset({"QUERY_ORDERS"}),
    "query_logistics": frozenset({"QUERY_LOGISTICS"}),
    "query_comment": frozenset({"QUERY_COMMENT"}),
    "query_coupon": frozenset({"QUERY_USER_COUPONS"}),
    "query_user_coupons": frozenset({"QUERY_USER_COUPONS"}),
    "query_refund_status": frozenset({"QUERY_REFUND_STATUS"}),
    "product_search": frozenset({"SEARCH_PRODUCTS", "GET_PRODUCT_DETAIL"}),
    "product_detail": frozenset({"GET_PRODUCT_DETAIL"}),
    "support_case_list": frozenset({"QUERY_SUPPORT_CASES"}),
    "support_case_detail": frozenset({"QUERY_SUPPORT_CASES"}),
}
_DYNAMIC_FACT_RE = re.compile(
    r"(?:订单|物流|退款|优惠券|库存|价格|工单).{0,20}"
    r"(?:待付款|已付款|已发货|已签收|已完成|处理中|成功|失败|剩余|￥|¥|\d+\.\d{2})"
)
_ORDER_STATUS_FACT_RE = re.compile(
    r"(?:订单|订单状态|履约状态).{0,24}(?:待付款|未付款|没付款|已付款|"
    r"待发货|未发货|没发货|尚未发货|已发货|运输中|已签收|已收货|"
    r"已完成|已删除|交易取消|交易关闭|已退款|部分退款|待评价|退款中|处理中)"
)
_ORDER_STATUS_VALUE_RE = re.compile(
    r"待付款|未付款|没付款|已付款|待发货|未发货|没发货|尚未发货|已发货|运输中|"
    r"已签收|已收货|已完成|已删除|交易取消|交易关闭|已退款|退款中|处理中|部分退款|待评价"
)
_ORDER_AMOUNT_FACT_RE = re.compile(
    r"(?:订单|订单项|退款|支付|金额|价格).{0,20}(?:金额|支付|退款|应付|实付)?"
    r".{0,12}(?:￥|¥|人民币|\d+(?:\.\d{1,2})?\s*元)"
)
_ORDER_PRODUCT_ASSERTION_RE = re.compile(
    r"(?:商品(?:名称)?|商品名)\s*(?:是|为|：|:)\s*[“\"']?([^。！？!?；;\n]{1,100})|"
    r"(?:买了|购买了|买的是|购买的是|已定位到)\s*[“\"']?([^。！？!?；;\n]{1,100})"
)
_ORDER_PROPERTY_FACT_RE = re.compile(
    r"(?:订单|订单项|商品).{0,20}(?:规格|属性|型号|颜色|尺码|版本).{0,20}(?:是|为|：|:)"
)
_ORDER_PAYMENT_FACT_RE = re.compile(
    r"(?:订单|支付|付款).{0,20}(?:支付方式|支付渠道|支付场景).{0,20}(?:是|为|：|:)|"
    r"(?:订单|订单项).{0,24}(?:使用|通过).{1,20}(?:支付|付款)"
)
_ORDER_QUANTITY_FACT_RE = re.compile(
    r"(?:订单|订单项|商品).{0,20}(?:数量|买了|购买了).{0,12}(?:件|个|份|\d+)"
)
_ORDER_TIME_FACT_RE = re.compile(
    r"(?:订单|下单|创建).{0,20}(?:时间|日期).{0,8}"
    r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}"
)
_DATE_VALUE_RE = re.compile(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}")
_MONEY_VALUE_RE = re.compile(
    r"(?:￥|¥|人民币)\s*(\d+(?:\.\d{1,2})?)|"
    r"(\d+(?:\.\d{1,2})?)\s*元"
)
_QUANTITY_VALUE_RE = re.compile(
    r"(?:数量\s*(?:是|为|：|:)?\s*(\d+(?:\.\d+)?))|"
    r"(\d+(?:\.\d+)?)\s*(?:件|个|份)"
)
_LOGISTICS_FACT_RE = re.compile(
    r"(?:物流|快递|包裹|运单|承运商).{0,24}"
    r"(?:待揽收|已揽收|运输中|派送中|已签收|异常|"
    r"状态\s*(?:是|为|：|:)|(?:最新)?位置\s*(?:是|为|：|:)|"
    r"承运商\s*(?:是|为|：|:)|运单号\s*(?:是|为|：|:)|"
    r"(?:是|为|：|:))"
)
_REFUND_STATUS_FACT_RE = re.compile(
    r"(?:退款|退费).{0,20}(?:退款中|处理中|成功|失败|已到账|未到账|"
    r"状态\s*(?:是|为|：|:)|金额\s*(?:是|为|：|:)|￥|¥|\d+(?:\.\d{1,2})?元)"
)
_INVENTORY_FACT_RE = re.compile(
    r"(?:库存|现货).{0,16}(?:剩余|有货|无货|缺货|充足|不足|售罄|"
    r"(?:是|为|：|:)\s*\d|\d+(?:\.\d+)?\s*(?:件|个|份))"
)
_PRICE_FACT_RE = re.compile(
    r"(?:商品|产品|该款|这款|SKU|价格|售价|到手价|最高价|最低价|"
    r"原价|基础价|起售价).{0,16}"
    r"(?:￥|¥|人民币|\d+(?:\.\d{1,2})?\s*元)"
)
_COUPON_FACT_RE = re.compile(
    r"(?:优惠券|券).{0,20}(?:可用|已使用|已过期|有效期|到期|"
    r"状态\s*(?:是|为|：|:)|共\s*\d+\s*张|有\s*\d+\s*张|"
    r"(?:面额|优惠金额|门槛|折扣).{0,8}(?:￥|¥|\d+(?:\.\d+)?\s*元))"
)
_SUPPORT_CASE_FACT_RE = re.compile(
    r"工单.{0,24}(?:待处理|处理中|已完成|已关闭|已创建|"
    r"状态\s*(?:是|为|：|:))"
)
_COMMENT_FACT_RE = re.compile(
    r"(?:评价|评论).{0,16}(?:待评价|已评价|已追评|状态\s*(?:是|为|：|:))"
)
_POLICY_CLAIM_RE = re.compile(
    r"(?:\d+|七|十五|三十)天(?:内|无理由)|无理由退货|运费由.{0,12}承担|"
    r"政策规定|平台规定|仅限.{0,16}(?:退款|退货|换货)|必须.{0,16}(?:凭证|条件)|"
    r"(?:符合|满足|具备|不符合|不满足).{0,8}(?:退款|退货|换货)(?:条件|资格)?|"
    r"(?:可以|能够|不能|不可).{0,10}(?:退款|退货|换货)"
)
_ACTION_CAPABILITY_PATTERNS: dict[str, re.Pattern[str]] = {
    "CANCEL_ORDER": re.compile(r"取消(?:订单)?"),
    "CONFIRM_RECEIPT": re.compile(r"确认收货"),
    "PRODUCT_REVIEW": re.compile(r"(?:首次)?评价"),
    "RECOMMENT": re.compile(r"追评"),
}
_POSITIVE_CAPABILITY_RE = re.compile(
    r"(?:可以|能够|能|允许|支持|具备|有权|有资格|符合|满足)"
)
_NEGATIVE_CAPABILITY_RE = re.compile(
    r"(?:不能|不可|不允许|不符合|不满足|无法|不支持|禁止|不得|"
    r"不具备|没有|无权|无资格|没资格)"
)
_OUTER_NEGATION_RE = re.compile(r"(?:不是|并不是|并非)")
_CAPABILITY_MARKER_RE = re.compile(
    r"(?:可以|能够|能|允许|可|支持|具备|有权|有资格|符合|满足|"
    r"不能|不可|不允许|不符合|不满足|无法|不支持|禁止|不得|"
    r"不具备|没有|无权|无资格|没资格|资格|条件|不是|并不是|并非)"
)
_AFTER_SALES_CAPABILITY_PATTERNS: dict[str, re.Pattern[str]] = {
    action: re.compile(label)
    for action, label in (("REFUND", "退款"), ("RETURN", "退货"))
}
_GENERAL_POLICY_RULE_RE = re.compile(
    r"(?:\d+|七|十五|三十)天(?:内|无理由)|政策规定|平台规定|运费由|必须.{0,16}凭证|"
    r"待付款订单.{0,16}取消|进入发货流程.{0,20}取消|已发货.{0,16}售后"
)
_ORDER_ID_IN_TEXT_RE = re.compile(
    r"订单(?:\s*(?:ID|号|编号))?\s*(?:是|为|[：:])?\s*"
    r"[\"'“”]?([A-Za-z0-9][A-Za-z0-9_-]{1,119})",
    re.I,
)
_ORDER_ITEM_ID_IN_TEXT_RE = re.compile(
    r"订单项(?:\s*(?:ID|编号))?\s*(?:是|为|[：:])?\s*"
    r"[\"'“”]?([A-Za-z0-9][A-Za-z0-9_-]{1,119})",
    re.I,
)
_PRODUCT_ID_IN_TEXT_RE = re.compile(
    r"(?:商品|产品)(?:ID|编号)\s*[：:]?\s*[\"'“”]?"
    r"([A-Za-z0-9][A-Za-z0-9_-]{1,119})|"
    r"(?:商品|产品)\s*[：:]?\s*[\"'“”]?"
    r"((?:P(?:RODUCT)?[-_]?\d[A-Za-z0-9_-]*|\d{4,}))"
    r"(?![A-Za-z0-9_-])",
    re.I,
)
_SUPPORT_CASE_ID_IN_TEXT_RE = re.compile(
    r"工单(?:ID|号|编号)?\s*[：:]?\s*[\"'“”]?"
    r"([A-Za-z0-9][A-Za-z0-9_-]{1,119})",
    re.I,
)
_COUPON_ID_IN_TEXT_RE = re.compile(
    r"(?:优惠券|券)(?:ID|编号)?\s*[：:]?\s*[\"'“”]?"
    r"([A-Za-z0-9][A-Za-z0-9_-]{0,119})",
    re.I,
)
_BARE_ENTITY_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z]{1,16}[-_]?\d[A-Za-z0-9_-]*"
    r"(?![A-Za-z0-9_-])"
)
_CASE_SPECIFIC_CAPABILITY_RE = re.compile(
    r"(?:该订单|此订单|您的订单|当前订单|本订单|这(?:个|笔|张)?订单|"
    r"本次(?:资格)?核验|本次|业务系统|订单号|订单项|"
    r"当前(?:可以|能够|能|允许|可|不能|不可|无法|支持|不支持|"
    r"禁止|不得|具备|不具备))"
)
_POLICY_ABSTENTION_RE = re.compile(
    r"(?:未找到|没有|缺少).{0,20}(?:政策|规则|依据|证据)|"
    r"(?:无法|不能|暂不能).{0,12}(?:确认|判断|核实).{0,16}(?:政策|条件|资格|是否)|"
    r"(?:政策|条件|资格).{0,16}(?:无法|不能|暂不能).{0,12}(?:确认|判断|核实)|"
    r"(?:不能|不会|不).{0,12}(?:给出|作出).{0,12}(?:确定|明确).{0,8}(?:政策|资格|结论)"
)
_POLICY_CLAUSE_RE = re.compile(r"[^。！？!?；;，,\n]+")
_ASSERTION_BOUNDARY_RE = re.compile(
    r"[。！？!?；;，,\n]+|但(?:是)?|不过|然而|可是|却|(?:而|并)(?=非)"
)
_NEGATED_VALUE_RE = re.compile(r"(?:其实|实际)?(?:并)?不是|并非|^\s*非")
_CLAIM_CONNECTOR_PATTERN = (
    r"(?:或者|并且|同时|而且|随后|然后|以及|或|也|又|且|"
    r"而(?!非)|并(?!非)|和|及|与|、)"
)
_CAPABILITY_CONNECTOR_RE = re.compile(_CLAIM_CONNECTOR_PATTERN)
_POLICY_UNCERTAINTY_PREFIX_RE = re.compile(
    r"(?:无法|暂时无法|暂无法|不能|暂不能).{0,12}(?:确认|判断|核实)"
)
_POLICY_ADVERSATIVE_RE = re.compile(r"(?:但|但是|不过|然而|可是|仍然|最终|其实)")
_RAG_CITATION_RE = re.compile(r"\[(\d+)]")
_GENERIC_POLICY_STATUS_RE = re.compile(
    r"(?:待付款订单|已发货通常|进入发货流程|取决于当前履约状态|"
    r"售后申请应从本人订单详情|退款申请应根据订单详情|"
    r"退款申请(?:会|应|需|需要)?根据商品类型、订单状态(?:和|及)实际情况(?:进行)?审核|"
    r"退款状态(?:可|可以|能|能够)(?:在|从)(?:本人)?订单详情(?:中)?(?:查看|查询|核对)|"
    r"优惠券.{0,20}(?:(?:每笔订单.{0,12})?(?:只能使用一张|不支持多张券叠加))|"
    r"优惠券.{0,24}(?:下单|提交订单).{0,12}(?:重新|再次)校验)"
)
_CASE_SPECIFIC_POLICY_RE = re.compile(
    r"(?:你当前|你的|我的|我这张|这张|该券|本券|账户(?:中|里)?的|持有的)"
    r".{0,12}(?:优惠券|券)|"
    r"(?:优惠券|券).{0,12}(?:你当前|你的|我的|这张|该券|本券|已过期|状态为)"
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
    "RECOMMENDATION_EVIDENCE_WITHOUT_CLAIM": (
        "当前候选的推荐依据无法由本次商品快照核验。请先查看商品详情后再决定。"
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
    "RAG_GENERATION_UNVERIFIED": (
        "本次回答未通过证据完整性校验，请稍后重试或回复“转人工”。"
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
        rag_generation_verified: bool = True,
        rag_source_refs: list[dict] | None = None,
        safe_fallback: str | None = None,
    ) -> VerificationResult:
        text = str(assistant or "").strip()
        called = frozenset(str(tool) for tool in tools_called or [])
        issues: list[VerificationIssue] = []
        if not rag_generation_verified:
            issues.append(
                VerificationIssue(
                    "RAG_GENERATION_UNVERIFIED",
                    "生成与修复均未通过当前证据契约",
                )
            )
        # New callers pass an explicit RAG channel.  The legacy ``source_refs``
        # argument remains supported for old replay fixtures, but a business
        # snapshot in ``businessSources`` must never satisfy a policy gate.
        effective_rag_refs = (
            rag_source_refs
            if rag_source_refs is not None
            else _legacy_rag_sources(source_refs)
        )
        source_count = _source_count(effective_rag_refs)
        dynamic_clauses = [
            clause
            for clause in _assertion_clauses(text)
            if _DYNAMIC_FACT_RE.search(clause)
        ]
        cited_write_policy_only = bool(dynamic_clauses) and all(
            _is_cited_write_confirmation_policy(clause, effective_rag_refs)
            for clause in dynamic_clauses
        )
        verified_action_card = (
            str(biz_type or "") == "action_confirm" and has_pending_action
        )
        selection_fact_text = (
            _order_selection_fact_text(text)
            if str(biz_type or "") == "order_selection"
            else None
        )
        verified_selection_card = bool(selection_fact_text)

        if (
            str(rag_evidence_state or "").upper() == "SUPPORTED"
            and source_count > 0
            and is_current_rag_abstention(text)
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
            (order_outcome in {"RESOLVED", "NO_ELIGIBLE"} or verified_selection_card)
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
            and not cited_write_policy_only
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

        fact_text = (
            _action_card_fact_text(text) if verified_action_card else None
        ) or selection_fact_text or text
        unsupported_order_fact = _unsupported_order_fact(fact_text, business_refs)
        unsupported_dynamic_fact = _unsupported_dynamic_business_fact(
            fact_text,
            business_refs,
            rag_source_count=source_count if rag_citation_required else 0,
            rag_source_refs=effective_rag_refs if rag_citation_required else [],
        )
        if unsupported_order_fact or unsupported_dynamic_fact:
            issues.append(
                VerificationIssue(
                    "DYNAMIC_FACT_WITHOUT_CLAIM",
                    unsupported_order_fact or unsupported_dynamic_fact or "",
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

        general_capability_claim = _has_general_capability_claim(text)
        unsupported_policy_claim = (
            _has_unsupported_policy_claim(text) or general_capability_claim
        )
        if (
            unsupported_policy_claim
            and _after_sales_claim_supported(text, business_refs)
            and not general_capability_claim
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
        unsupported_recommendation = _unsupported_recommendation_evidence(
            recommendation_candidates or [], business_refs
        )
        if unsupported_recommendation:
            issues.append(
                VerificationIssue(
                    "RECOMMENDATION_EVIDENCE_WITHOUT_CLAIM",
                    unsupported_recommendation,
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
        or ref.get("authoritative", True) is False
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
    item_ids = _text_order_item_ids(text)
    if len(order_ids) > 1 or len(item_ids) > 1:
        return []
    refs = [
        ref
        for ref in _business_ref_rows(source_refs)
        if _trusted_order_ref(ref)
    ]
    if order_ids:
        matched = [
            ref
            for ref in refs
            if str(ref.get("orderId") or ref.get("id") or "").strip()
            in order_ids
        ]
        return matched if len(matched) == 1 else []
    if item_ids:
        matched = [
            ref
            for ref in refs
            if item_ids.intersection(
                {
                    str(claim.get("subjectId") or "")
                    for claim in ref.get("claims") or []
                    if isinstance(claim, dict)
                    and claim.get("subjectType") == "order_item"
                    and claim.get("factPath") == "order_item.orderItemId"
                }
            )
        ]
        return matched if len(matched) == 1 else []
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


def _normalized_scalar(value: Any) -> str:
    raw = "" if value is None else str(value)
    return re.sub(r"[\s，,。；;：:'\"“”‘’_-]+", "", raw).casefold()


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _matched_decimals(pattern: re.Pattern[str], text: str) -> set[Decimal]:
    values: set[Decimal] = set()
    for match in pattern.finditer(text or ""):
        raw = next((group for group in match.groups() if group), None)
        if (value := _decimal(raw)) is not None:
            values.add(value)
    return values


def _claim_values(
    claims: list[dict[str, Any]], paths: set[str]
) -> list[Any]:
    return [
        claim.get("value")
        for claim in claims
        if claim.get("factPath") in paths and claim.get("value") not in (None, "")
    ]


def _values_mentioned(values: list[Any], text: str) -> bool:
    normalized = _normalized_scalar(text)
    return any(
        (candidate := _normalized_scalar(value)) and candidate in normalized
        for value in values
    )


def _numeric_values_match(
    values: list[Any], pattern: re.Pattern[str], text: str
) -> bool:
    asserted = _matched_decimals(pattern, text)
    expected = {value for raw in values if (value := _decimal(raw)) is not None}
    return bool(asserted) and asserted.issubset(expected)


def _payment_assertions(text: str) -> list[str]:
    boundary = r"(?=而非|但|而|和|及|、|，|,|。|；|;|\s|$)"
    labelled = re.compile(
        r"(?:支付方式|支付渠道|支付场景)\s*(?:是|为|：|:)\s*"
        r"([^。！？!?；;，,\s]{1,24}?)" + boundary
    )
    natural = re.compile(
        r"(?:使用|通过|和|及|、)\s*"
        r"([\u4e00-\u9fffA-Za-z0-9_-]{1,16}?(?:支付|付款))"
        + boundary
    )
    return [*labelled.findall(text or ""), *natural.findall(text or "")]


def _payment_values_match(values: list[Any], clause: str) -> bool:
    asserted = [_normalized_scalar(value) for value in _payment_assertions(clause)]
    expected = [_normalized_scalar(value) for value in values if str(value).strip()]
    return bool(asserted) and all(
        any(value == candidate or value in candidate or candidate in value for candidate in expected)
        for value in asserted
    )


def _item_claims_for_clause(
    claims: list[dict[str, Any]], clause: str
) -> list[dict[str, Any]]:
    item_ids = _text_order_item_ids(clause)
    if not item_ids:
        return claims
    return [
        claim
        for claim in claims
        if claim.get("subjectType") != "order_item"
        or str(claim.get("subjectId") or "") in item_ids
    ]


def _item_scope_ambiguous(
    claims: list[dict[str, Any]], clause: str, paths: set[str]
) -> bool:
    if _text_order_item_ids(clause):
        return False
    return len(
        {
            str(claim.get("subjectId") or "")
            for claim in claims
            if claim.get("subjectType") == "order_item"
            and claim.get("factPath") in paths
            and str(claim.get("subjectId") or "")
        }
    ) > 1


def _order_status_values(claims: list[dict[str, Any]]) -> list[str]:
    values = [
        str(value)
        for value in _claim_values(claims, {"order.orderStatusName"})
        if str(value).strip()
    ]
    for raw in _claim_values(claims, {"order.orderStatus"}):
        try:
            name = ORDER_STATUS_NAMES.get(int(raw))
        except (TypeError, ValueError):
            name = None
        if name:
            values.append(name)
    return values


def _order_status_matches(claims: list[dict[str, Any]], clause: str) -> bool:
    status_values = {
        _normalized_scalar(value) for value in _order_status_values(claims)
    } - {""}
    if len(status_values) != 1:
        return False
    asserted = _ORDER_STATUS_VALUE_RE.findall(clause)
    aliases = {
        "未付款": "待付款",
        "没付款": "待付款",
        "未发货": "待发货",
        "没发货": "待发货",
        "尚未发货": "待发货",
    }
    matches = [
        any(
            _normalized_scalar(aliases.get(value, value)) in status
            for status in status_values
        )
        for value in asserted
    ]
    if _NEGATED_VALUE_RE.search(clause) or re.search(r"不属于", clause):
        return bool(matches) and not any(matches)
    return bool(matches) and all(matches)


def _unsupported_order_fact(
    text: str, source_refs: list[dict] | dict | None
) -> str | None:
    """Reject an order answer whose concrete field lacks a Java claim.

    This closes the gap between “the response has a valid order id” and “the
    response is allowed to invent the product, status or payment details of
    that order”.  Generic policy prose without a concrete order id remains on
    the RAG policy path.
    """

    answer_order_context = bool(
        _text_order_ids(text) or _text_order_item_ids(text) or re.search(r"订单", text)
    )
    prior_fields: set[str] = set()
    for clause in _assertion_clauses(text):
        if not clause:
            continue
        order_specific = bool(
            answer_order_context
            or _text_order_ids(clause)
            or re.search(r"订单|订单项", clause)
        )
        refund_status_clause = bool(
            _dynamic_status_assertions(clause, r"退款|退费")
        )
        negated_value = bool(_NEGATED_VALUE_RE.search(clause))
        inherited_value = not re.match(r"^\s*非", clause)
        status_asserted = bool(
            (
                _ORDER_STATUS_FACT_RE.search(clause)
                or (answer_order_context and _ORDER_STATUS_VALUE_RE.search(clause))
            )
            and not re.search(r"物流|快递|包裹|运单|退款(?:状态|进度|金额)|退费", clause)
            and not refund_status_clause
        )
        amount_asserted = bool(
            _ORDER_AMOUNT_FACT_RE.search(clause)
            and not re.search(r"退款|退费", clause)
            and not (
                re.search(r"(?:商品|产品|该款|这款|SKU|售价|到手价)", clause)
                and not order_specific
            )
        ) or bool(
            (inherited_value or negated_value)
            and "amount" in prior_fields
            and _MONEY_VALUE_RE.search(clause)
        )
        product = (
            _mentioned_order_product(clause)
            if order_specific or re.search(r"买了|购买了|买的是|购买的是", clause)
            else None
        )
        property_asserted = bool(
            _ORDER_PROPERTY_FACT_RE.search(clause) and order_specific
        )
        explicit_payment = bool(_ORDER_PAYMENT_FACT_RE.search(clause))
        inherited_payment = bool(
            (inherited_value or negated_value)
            and "payment" in prior_fields
            and re.search(r"支付|付款", clause)
        )
        payment_asserted = explicit_payment or inherited_payment
        quantity_asserted = bool(
            _ORDER_QUANTITY_FACT_RE.search(clause)
            and (
                order_specific
                or re.search(r"买了|购买了|买的是|购买的是", clause)
            )
        ) or bool(
            (inherited_value or negated_value)
            and "quantity" in prior_fields
            and _QUANTITY_VALUE_RE.search(clause)
        )
        time_asserted = bool(_ORDER_TIME_FACT_RE.search(clause))
        if not any(
            (
                status_asserted,
                amount_asserted,
                bool(product),
                property_asserted,
                payment_asserted,
                quantity_asserted,
                time_asserted,
            )
        ):
            continue
        if (
            not _text_order_ids(clause)
            and _GENERAL_POLICY_RULE_RE.search(clause)
        ):
            continue
        refs = _trusted_order_refs_for_text(source_refs, clause)
        if not refs:
            return "回答中的订单动态事实缺少 authoritative、matched 的 Java 订单 ref"
        claims = _item_claims_for_clause(_order_claims(refs), clause)
        if status_asserted and not _order_status_matches(claims, clause):
            return "回答中的订单状态与 Java 动态字段 claim 值不一致"
        if amount_asserted:
            item_amount = bool(
                _text_order_item_ids(clause)
                or re.search(r"订单项|商品", clause)
            )
            paths = {"order_item.itemAmount"} if item_amount else {"order.amount"}
            if item_amount and _item_scope_ambiguous(claims, clause, paths):
                return "回答中的订单项金额未绑定唯一 orderItemId"
            amount_matches = _numeric_values_match(
                _claim_values(claims, paths), _MONEY_VALUE_RE, clause
            )
            if negated_value and amount_matches:
                return "回答后句否定了已核验的订单金额"
            if not negated_value and not amount_matches:
                return "回答中的订单金额与 Java 动态字段 claim 值不一致"
        if product:
            if _item_scope_ambiguous(
                claims, clause, {"order_item.productName"}
            ):
                return "回答中的商品名未绑定唯一 orderItemId"
            product_values = _claim_values(claims, {"order_item.productName"})
            normalized_product = _normalized_scalar(product)
            if not normalized_product or not any(
                normalized_product in _normalized_scalar(value)
                or _normalized_scalar(value) in normalized_product
                for value in product_values
            ):
                return "回答中的商品名与订单项动态字段 claim 不一致"
        if property_asserted:
            property_paths = {"order_item.propertyInfo", "order_item.productName"}
            if _item_scope_ambiguous(claims, clause, property_paths):
                return "回答中的商品规格未绑定唯一 orderItemId"
            if not _values_mentioned(_claim_values(claims, property_paths), clause):
                return "回答中的商品规格与订单项动态字段 claim 值不一致"
        if payment_asserted:
            payment_values = _claim_values(
                claims, {"order.payScene", "order.payChannel"}
            )
            if negated_value and _values_mentioned(payment_values, clause):
                return "回答后句否定了已核验的支付方式"
            if not negated_value:
                payment_matches = (
                    _payment_values_match(payment_values, clause)
                    if explicit_payment
                    else _values_mentioned(payment_values, clause)
                )
                if not payment_matches:
                    return "回答中的支付信息与 Java 动态字段 claim 值不一致"
        if quantity_asserted:
            quantity_paths = {"order_item.buyCount"}
            if _item_scope_ambiguous(claims, clause, quantity_paths):
                return "回答中的商品数量未绑定唯一 orderItemId"
            quantity_matches = _numeric_values_match(
                _claim_values(claims, quantity_paths),
                _QUANTITY_VALUE_RE,
                clause,
            )
            if negated_value and quantity_matches:
                return "回答后句否定了已核验的商品数量"
            if not negated_value and not quantity_matches:
                return "回答中的商品数量与订单项动态字段 claim 值不一致"
        if time_asserted and not _values_mentioned(
            _claim_values(claims, {"order.orderTime"}), clause
        ):
            return "回答中的下单时间与 Java 动态字段 claim 值不一致"
        if not negated_value:
            if status_asserted:
                prior_fields.add("status")
            if amount_asserted:
                prior_fields.add("amount")
            if payment_asserted:
                prior_fields.add("payment")
            if quantity_asserted:
                prior_fields.add("quantity")
    return None


def _trusted_business_refs(
    source_refs: list[dict] | dict | None,
    *,
    ref_type: str,
    sources: set[str],
    clause: str,
) -> list[dict[str, Any]]:
    order_ids = _text_order_ids(clause)
    item_ids = _text_order_item_ids(clause)
    product_ids = _text_product_ids(clause)
    case_ids = _text_support_case_ids(clause)
    coupon_ids = _text_coupon_ids(clause)
    if any(
        len(ids) > 1
        for ids in (order_ids, item_ids, product_ids, case_ids, coupon_ids)
    ):
        return []
    identity_fields = {
        "logistics": ("orderId",),
        "refund": ("orderId", "orderItemId"),
        "product": ("productId", "id"),
        "coupon": ("id", "couponId"),
        "support_case": ("id", "caseId", "caseNo"),
        "comment": ("orderId",),
    }.get(ref_type, ("id",))
    refs: list[dict[str, Any]] = []
    for ref in _business_ref_rows(source_refs):
        if (
            str(ref.get("type") or "").lower() != ref_type
            or str(ref.get("source") or "") not in sources
            or ref.get("matched", True) is False
            or ref.get("authoritative", True) is False
        ):
            continue
        if not any(str(ref.get(field) or "").strip() for field in identity_fields):
            continue
        if order_ids and str(ref.get("orderId") or "") not in order_ids:
            continue
        if item_ids and str(ref.get("orderItemId") or "") not in item_ids:
            continue
        if product_ids and str(ref.get("productId") or ref.get("id") or "") not in product_ids:
            continue
        if case_ids and str(
            ref.get("caseNo") or ref.get("caseId") or ref.get("id") or ""
        ) not in case_ids:
            continue
        if coupon_ids and not {
            str(ref.get("id") or ""),
            str(ref.get("couponId") or ""),
        }.intersection(coupon_ids):
            continue
        refs.append(ref)
    if ref_type in {"logistics", "refund", "comment"} and len(refs) != 1:
        return []
    if ref_type == "product" and not product_ids and len(refs) != 1:
        return []
    if ref_type == "support_case" and not case_ids and len(refs) != 1:
        return []
    return refs


def _ref_values(refs: list[dict[str, Any]], *fields: str) -> list[Any]:
    return [
        ref.get(field)
        for ref in refs
        for field in fields
        if ref.get(field) not in (None, "")
    ]


def _dynamic_status_assertions(text: str, subject_pattern: str) -> list[str]:
    suffix = (
        rf"(?={_CLAIM_CONNECTOR_PATTERN}|但|不过|然而|可是|却|"
        r"，|,|。|；|;|！|!|？|\?|\s|$)"
    )
    explicit = re.compile(
        rf"(?:{subject_pattern}).{{0,24}}?"
        r"状态\s*(?:是|为|：|:)?\s*"
        r"([\u4e00-\u9fffA-Za-z0-9_-]{1,16}?)"
        + suffix
    )
    implicit = re.compile(
        rf"(?:{subject_pattern}).{{0,12}}?"
        r"((?:已|待|未|正)[\u4e00-\u9fffA-Za-z0-9_-]{1,10}?|"
        r"处理中|成功|失败|异常|冻结|驳回|取消|关闭|完成|可用)"
        + suffix
    )
    if not re.search(rf"(?:{subject_pattern})", text or ""):
        return []
    asserted = [*explicit.findall(text or ""), *implicit.findall(text or "")]
    continued = re.compile(
        _CLAIM_CONNECTOR_PATTERN + r"\s*"
        r"(?:其实|实际)?\s*"
        r"((?:已|待|未|正)[\u4e00-\u9fffA-Za-z0-9_-]{1,10}?|"
        r"处理中|成功|失败|异常|冻结|驳回|取消|关闭|完成|可用|"
        r"运输中|派送中)"
        + suffix
    )
    return [*asserted, *continued.findall(text or "")]


def _status_values_match(values: list[Any], asserted: list[str]) -> bool:
    expected = {_normalized_scalar(value) for value in values} - {""}
    if len(expected) != 1:
        return False
    return bool(asserted) and all(
        any(
            (claim := _normalized_scalar(value)) == candidate
            or claim in candidate
            for candidate in expected
        )
        for value in asserted
    )


def _refund_status_matches(refs: list[dict[str, Any]], asserted: list[str]) -> bool:
    raw = _ref_values(refs, "refundStatus")
    if len(raw) != 1:
        return False
    aliases = {
        "PENDING_PAYMENT": ("等待原路退款",),
        "PAYMENT_CONFIRMED": ("退款资金已确认", "已确认"),
        "STOCK_PENDING": ("退款已受理", "库存处理中", "处理中"),
        "COMPLETED": ("退款已完成", "已完成", "成功"),
        "MANUAL_REVIEW": ("退款需人工复核", "需人工复核"),
        "REJECTED": ("退款申请已驳回", "已驳回", "审核拒绝"),
    }.get(str(raw[0]).upper(), (str(raw[0]),))
    expected = [_normalized_scalar(value) for value in aliases]
    return bool(asserted) and all(
        any(
            (claim := _normalized_scalar(value)) == candidate
            or claim in candidate
            or candidate in claim
            for candidate in expected
        )
        for value in asserted
    )


def _ref_claim_values(
    refs: list[dict[str, Any]], paths: set[str]
) -> list[Any]:
    values: list[Any] = []
    for ref in refs:
        source = str(ref.get("source") or "")
        source_ids = {
            str(ref.get(key) or "")
            for key in ("id", "productId", "offerSnapshotId", "orderId", "orderItemId")
            if str(ref.get(key) or "")
        }
        values.extend(
            claim.get("value")
            for claim in ref.get("claims") or []
            if isinstance(claim, dict)
            and claim.get("factPath") in paths
            and claim.get("sourceType") == source
            and str(claim.get("sourceId") or "") in source_ids
            and claim.get("value") not in (None, "")
        )
    return values


def _unsupported_dynamic_business_fact(
    text: str,
    source_refs: list[dict] | dict | None,
    *,
    rag_source_count: int = 0,
    rag_source_refs: list[dict] | None = None,
) -> str | None:
    """Bind each non-order dynamic assertion to its Java-owned ref and value."""

    prior_price_paths: set[str] | None = None
    prior_coupon_amount_field: str | None = None
    prior_coupon_expiry = False
    prior_logistics_status: list[str] = []
    prior_refund_status: list[str] = []
    prior_coupon_status: list[str] = []
    prior_support_status: list[str] = []
    prior_comment_status: list[str] = []
    prior_inventory_status: list[str] = []
    for clause in _assertion_clauses(text):
        if not clause:
            continue
        citations = [int(value) for value in _RAG_CITATION_RE.findall(clause)]
        generic_policy_match = _GENERIC_POLICY_STATUS_RE.search(clause)
        generic_policy_remainder = ""
        if generic_policy_match is not None:
            generic_policy_remainder = (
                clause[: generic_policy_match.start()]
                + clause[generic_policy_match.end() :]
            )
            generic_policy_remainder = _RAG_CITATION_RE.sub(
                "", generic_policy_remainder
            )
            generic_policy_remainder = re.sub(
                r"^(?:一般(?:来说)?|通常|政策(?:规定)?|平台(?:规则)?)[，,\s]*",
                "",
                generic_policy_remainder,
            ).strip(" ，,。；;、且和及")
        generic_policy_clause = bool(
            citations
            and all(0 < value <= rag_source_count for value in citations)
            and generic_policy_match
            and not generic_policy_remainder
            and not _CASE_SPECIFIC_POLICY_RE.search(clause)
            and not any(
                (
                    _text_order_ids(clause),
                    _text_order_item_ids(clause),
                    _text_product_ids(clause),
                    _text_coupon_ids(clause),
                    _text_support_case_ids(clause),
                )
            )
        )
        generic_policy_clause = generic_policy_clause or _is_cited_write_confirmation_policy(
            clause, rag_source_refs or []
        )
        negated_value = bool(_NEGATED_VALUE_RE.search(clause))
        logistics_statuses = _dynamic_status_assertions(
            clause, r"物流|快递|包裹"
        )
        inherited_logistics = bool(
            negated_value and _values_mentioned(prior_logistics_status, clause)
        )
        if inherited_logistics:
            return "回答后句否定了已核验的物流状态"
        if logistics_statuses or _LOGISTICS_FACT_RE.search(clause):
            refs = _trusted_business_refs(
                source_refs,
                ref_type="logistics",
                sources={"JAVA_LOGISTICS_SERVICE"},
                clause=clause,
            )
            if not refs:
                return "物流动态事实缺少 authoritative、matched 的 Java logistics ref"
            if logistics_statuses and not _status_values_match(
                _ref_values(refs, "status"), logistics_statuses
            ):
                return "回答中的物流状态与 Java logistics ref 值不一致"
            if logistics_statuses and not negated_value:
                prior_logistics_status = logistics_statuses
            for label, field in (
                (r"承运商\s*(?:是|为|：|:)", "carrier"),
                (r"(?:快递|物流)\s*(?:是|为|：|:)", "carrier"),
                (r"运单号\s*(?:是|为|：|:)", "trackingNo"),
                (r"(?:最新)?位置\s*(?:是|为|：|:)", "latestLocation"),
            ):
                if re.search(label, clause) and not _values_mentioned(
                    _ref_values(refs, field), clause
                ):
                    return f"回答中的物流字段 {field} 与 Java ref 值不一致"

        refund_statuses = _dynamic_status_assertions(clause, r"退款|退费")
        inherited_refund = bool(
            negated_value and _values_mentioned(prior_refund_status, clause)
        )
        if inherited_refund:
            return "回答后句否定了已核验的退款状态"
        if not generic_policy_clause and (
            refund_statuses or _REFUND_STATUS_FACT_RE.search(clause)
        ):
            refs = _trusted_business_refs(
                source_refs,
                ref_type="refund",
                sources={"JAVA_REFUND_SERVICE"},
                clause=clause,
            )
            if not refs:
                return "退款动态事实缺少 authoritative、matched 的 Java refund ref"
            if refund_statuses and not _refund_status_matches(
                refs, refund_statuses
            ):
                return "回答中的退款状态与 Java refund ref 值不一致"
            if refund_statuses and not negated_value:
                prior_refund_status = refund_statuses
            if _MONEY_VALUE_RE.search(clause) and not _numeric_values_match(
                _ref_values(refs, "refundAmount"), _MONEY_VALUE_RE, clause
            ):
                return "回答中的退款金额与 Java refund ref 值不一致"

        inventory_statuses = re.findall(
            r"有货|充足|无货|缺货|售罄", clause
        )
        inherited_inventory = bool(
            negated_value and _values_mentioned(prior_inventory_status, clause)
        )
        if inherited_inventory:
            return "回答后句否定了已核验的库存状态"
        if inventory_statuses or _INVENTORY_FACT_RE.search(clause):
            refs = _trusted_business_refs(
                source_refs,
                ref_type="product",
                sources={"JAVA_GATEWAY", "JAVA_PRODUCT_SERVICE"},
                clause=clause,
            )
            if not refs:
                return "库存事实缺少 authoritative、matched 的 Java product ref"
            stock_values = [
                *_ref_values(refs, "stock"),
                *_ref_claim_values(refs, {"offer.stock"}),
            ]
            if _QUANTITY_VALUE_RE.search(clause) and not _numeric_values_match(
                stock_values, _QUANTITY_VALUE_RE, clause
            ):
                return "回答中的库存数量与 Java offer claim 值不一致"
            availability = {
                _normalized_scalar(value)
                for value in [
                    *_ref_values(refs, "availability"),
                    *_ref_claim_values(
                        refs, {"offer.availability", "offer.inStock"}
                    ),
                ]
            }
            stock = {_decimal(value) for value in stock_values} - {None}
            positive_signal = bool(
                {"true", "onsale"}.intersection(availability)
                or any(value > 0 for value in stock)
            )
            negative_signal = bool(
                {"false", "outofstock", "unavailable"}.intersection(availability)
                or (stock and all(value <= 0 for value in stock))
            )
            availability_asserted = bool(
                re.search(r"有货|充足|无货|缺货|售罄", clause)
            )
            if availability_asserted and positive_signal and negative_signal:
                return "Java offer claim 的库存与可售状态冲突"
            if re.search(r"有货|充足", clause) and not positive_signal:
                return "回答中的库存可用状态与 Java offer claim 值不一致"
            if re.search(r"无货|缺货|售罄", clause) and not negative_signal:
                return "回答中的库存不可用状态与 Java offer claim 值不一致"
            if inventory_statuses and not negated_value:
                prior_inventory_status = inventory_statuses

        price_asserted = bool(
            _PRICE_FACT_RE.search(clause)
            and not re.search(
                r"订单|订单项|退款|退费|支付|实付|应付", clause
            )
        ) or bool(
            negated_value
            and prior_price_paths
            and _MONEY_VALUE_RE.search(clause)
        )
        if price_asserted:
            refs = _trusted_business_refs(
                source_refs,
                ref_type="product",
                sources={"JAVA_GATEWAY", "JAVA_PRODUCT_SERVICE"},
                clause=clause,
            )
            if not refs:
                return "价格事实缺少 authoritative、matched 的 Java product ref"
            if negated_value and prior_price_paths:
                paths = prior_price_paths
            elif re.search(r"到手价|实付价", clause):
                paths = {"offer.estimatedPayable"}
            elif re.search(r"最高价", clause):
                paths = {"offer.maxPrice"}
            elif re.search(r"最低价|起售价", clause):
                paths = {"offer.minPrice"}
            elif re.search(r"原价|基础价", clause):
                paths = {"offer.basePrice"}
            else:
                paths = {"offer.price"}
            values = _ref_claim_values(refs, paths)
            if paths == {"offer.price"}:
                values = [*_ref_values(refs, "price"), *values]
            price_matches = _numeric_values_match(
                values, _MONEY_VALUE_RE, clause
            )
            if negated_value and price_matches:
                return "回答后句否定了已核验的价格"
            if not negated_value and not price_matches:
                return "回答中的价格与 Java offer claim 值不一致"
            if not negated_value:
                prior_price_paths = paths

        coupon_statuses = _dynamic_status_assertions(clause, r"优惠券|券")
        inherited_coupon_status = bool(
            negated_value and _values_mentioned(prior_coupon_status, clause)
        )
        if inherited_coupon_status:
            return "回答后句否定了已核验的优惠券状态"
        inherited_coupon_amount = bool(
            negated_value
            and prior_coupon_amount_field
            and _MONEY_VALUE_RE.search(clause)
        )
        inherited_coupon_expiry = bool(
            negated_value and prior_coupon_expiry and _DATE_VALUE_RE.search(clause)
        )
        if (
            not generic_policy_clause
            and (
                coupon_statuses
                or _COUPON_FACT_RE.search(clause)
                or inherited_coupon_amount
                or inherited_coupon_expiry
            )
        ):
            refs = _trusted_business_refs(
                source_refs,
                ref_type="coupon",
                sources={"JAVA_COUPON_SERVICE"},
                clause=clause,
            )
            if not refs:
                return "优惠券事实缺少 authoritative、matched 的 Java coupon ref"
            count = re.search(r"(?:共|有)\s*(\d+)\s*张", clause)
            if count and int(count.group(1)) != len(refs):
                return "回答中的优惠券数量与 Java coupon refs 不一致"
            dates = _DATE_VALUE_RE.findall(clause)
            date_matches = bool(dates) and all(
                any(date in str(value) for value in _ref_values(refs, "validEndTime"))
                for date in dates
            )
            if inherited_coupon_expiry and date_matches:
                return "回答后句否定了已核验的优惠券到期时间"
            if dates and not negated_value and not date_matches:
                return "回答中的优惠券到期时间与 Java coupon ref 值不一致"
            if _MONEY_VALUE_RE.search(clause):
                field = (
                    prior_coupon_amount_field
                    if inherited_coupon_amount
                    else "thresholdAmount"
                    if "门槛" in clause
                    else "discountAmount"
                )
                amount_matches = _numeric_values_match(
                    _ref_values(refs, field), _MONEY_VALUE_RE, clause
                )
                if inherited_coupon_amount and amount_matches:
                    return "回答后句否定了已核验的优惠券金额"
                if not negated_value and not amount_matches:
                    return "回答中的优惠券金额与 Java coupon ref 值不一致"
                if not negated_value:
                    prior_coupon_amount_field = field
            if dates and not negated_value:
                prior_coupon_expiry = True
            status_names = {0: "可用", 1: "已使用", 2: "已过期"}
            values = [status_names.get(value, value) for value in _ref_values(refs, "status")]
            if coupon_statuses and not _status_values_match(values, coupon_statuses):
                return "回答中的优惠券状态与 Java coupon ref 值不一致"
            if coupon_statuses and not negated_value:
                prior_coupon_status = coupon_statuses

        support_statuses = _dynamic_status_assertions(clause, r"工单")
        inherited_support = bool(
            negated_value and _values_mentioned(prior_support_status, clause)
        )
        if inherited_support:
            return "回答后句否定了已核验的工单状态"
        if support_statuses or _SUPPORT_CASE_FACT_RE.search(clause):
            refs = _trusted_business_refs(
                source_refs,
                ref_type="support_case",
                sources={"JAVA_SUPPORT_CASE_SERVICE"},
                clause=clause,
            )
            if not refs:
                return "工单事实缺少 authoritative、matched 的 Java support-case ref"
            if support_statuses and not _status_values_match(
                _ref_values(refs, "status"), support_statuses
            ):
                return "回答中的工单状态与 Java support-case ref 值不一致"
            if support_statuses and not negated_value:
                prior_support_status = support_statuses

        comment_statuses = _dynamic_status_assertions(clause, r"评价|评论")
        inherited_comment = bool(
            negated_value and _values_mentioned(prior_comment_status, clause)
        )
        if inherited_comment:
            return "回答后句否定了已核验的评价状态"
        if comment_statuses or _COMMENT_FACT_RE.search(clause):
            refs = _trusted_business_refs(
                source_refs,
                ref_type="comment",
                sources={"JAVA_COMMENT_SERVICE"},
                clause=clause,
            )
            if not refs:
                return "评价事实缺少 authoritative、matched 的 Java comment ref"
            if comment_statuses and not _status_values_match(
                _ref_values(refs, "commentStatus", "status"), comment_statuses
            ):
                return "回答中的评价状态与 Java comment ref 值不一致"
            if comment_statuses and not negated_value:
                prior_comment_status = comment_statuses
    return None


def _is_cited_write_confirmation_policy(
    clause: str, rag_source_refs: list[dict]
) -> bool:
    citations = [int(value) for value in _RAG_CITATION_RE.findall(clause)]
    fact_ids = {
        str(fact_id)
        for citation in citations
        if 0 < citation <= len(rag_source_refs)
        for fact_id in rag_source_refs[citation - 1].get("factIds") or []
    }
    return bool(
        citations
        and fact_ids.intersection(
            {
                "ai.capability_and_confirmation",
                "privacy.handoff_and_write_confirmation",
                "review.ai_write_boundary",
            }
        )
        and re.search(r"确认|待确认", clause)
        and re.search(r"AI|助手|系统|写操作|退款|取消|下单|订单", clause)
        and not re.search(
            r"你当前|你的|我的|该订单|当前订单|本订单|这笔订单|订单号|订单项",
            clause,
        )
    )


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
            or ref.get("matched", True) is False
            or ref.get("authoritative", True) is False
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
            or ref.get("matched", True) is False
            or ref.get("authoritative", True) is False
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
            r"(?<![A-Z0-9_-])(?:SM|SO)\d[A-Z0-9_-]{2,119}(?![A-Z0-9_-])|"
            r"(?<![A-Z0-9_-])(?:ORD|ORDER)[-_]?[A-Z0-9_-]*"
            r"\d[A-Z0-9_-]*(?![A-Z0-9_-])|"
            r"(?<![A-Z0-9_-])O\d[A-Z0-9_-]*(?![A-Z0-9_-])",
            text or "",
            re.I,
        )
    )
    return {value for value in ids if value} - _text_order_item_ids(text)


def _text_order_item_ids(text: str) -> set[str]:
    ids = {
        value.strip()
        for value in _ORDER_ITEM_ID_IN_TEXT_RE.findall(text or "")
        if value.strip()
    }
    ids.update(
        re.findall(
            r"(?<![A-Za-z0-9_-])(?:I|ITEM)\d[A-Za-z0-9_-]*"
            r"(?![A-Za-z0-9_-])|"
            r"(?<![A-Za-z0-9_-])SMITEM[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*"
            r"(?![A-Za-z0-9_-])",
            text or "",
            re.I,
        )
    )
    return ids


def _text_product_ids(text: str) -> set[str]:
    return {
        value.strip()
        for groups in _PRODUCT_ID_IN_TEXT_RE.findall(text or "")
        for value in groups
        if value.strip()
    }


def _text_support_case_ids(text: str) -> set[str]:
    return {
        value.strip()
        for value in _SUPPORT_CASE_ID_IN_TEXT_RE.findall(text or "")
        if value.strip()
    }


def _text_coupon_ids(text: str) -> set[str]:
    return {
        value.strip()
        for value in _COUPON_ID_IN_TEXT_RE.findall(text or "")
        if value.strip()
    }


def _capability_target_ids(
    clause: str, action: str
) -> tuple[set[str], set[str]]:
    order_ids = _text_order_ids(clause)
    item_ids = _text_order_item_ids(clause)
    bare = set(_BARE_ENTITY_ID_RE.findall(clause or "")) - order_ids - item_ids
    if order_ids:
        order_ids.update(bare)
    elif item_ids:
        item_ids.update(bare)
    elif bare:
        item_scoped = action in {"PRODUCT_REVIEW", "RECOMMENT"} or all(
            value.upper().startswith(("I", "ITEM", "SMITEM"))
            for value in bare
        )
        (item_ids if item_scoped else order_ids).update(bare)
    return order_ids, item_ids


def _assertion_clauses(text: str) -> list[str]:
    return [
        clause.strip()
        for clause in _ASSERTION_BOUNDARY_RE.split(text or "")
        if clause.strip()
    ]


def _action_card_fact_text(text: str) -> str | None:
    """Give server-built action-card values an explicit order/item scope."""

    try:
        card = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(card, dict) or card.get("type") != "ACTION_CONFIRM":
        return None
    order_id = str(card.get("orderId") or "").strip()
    items = [item for item in card.get("items") or [] if isinstance(item, dict)]
    facts: list[str] = []
    if order_id and card.get("orderAmount") is not None:
        facts.append(f"订单 {order_id} 订单金额 {card['orderAmount']} 元")
    if order_id and card.get("payScene"):
        facts.append(f"订单 {order_id} 支付场景为 {card['payScene']}")
    for item in items:
        item_id = str(item.get("orderItemId") or "").strip()
        if not item_id:
            continue
        for label, key, suffix in (
            ("商品名称为", "productName", ""),
            ("商品规格为", "propertyInfo", ""),
            ("订单项金额", "itemAmount", " 元"),
            ("商品数量", "buyCount", " 件"),
        ):
            if item.get(key) not in (None, ""):
                facts.append(f"订单项 {item_id} {label} {item[key]}{suffix}")
    for detail in card.get("details") or []:
        if not isinstance(detail, dict) or detail.get("value") in (None, ""):
            continue
        label = str(detail.get("label") or "").strip()
        value = str(detail["value"]).strip()
        if label == "退款金额" and len(items) == 1 and items[0].get("orderItemId"):
            facts.append(f"订单项 {items[0]['orderItemId']} 订单项金额 {value}")
        elif order_id:
            facts.append(f"订单 {order_id} {label} {value}")
    return "。".join(facts) or None


def _order_selection_fact_text(text: str) -> str | None:
    """Expand each stored selection candidate into independently scoped facts."""

    try:
        card = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(card, dict) or card.get("type") != "ORDER_SELECTION":
        return None
    facts: list[str] = []
    for candidate in card.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        order_id = str(candidate.get("orderId") or "").strip()
        item_id = str(candidate.get("orderItemId") or "").strip()
        if not order_id:
            continue
        for label, key in (
            ("订单状态为", "orderStatusName"),
            ("支付场景为", "payScene"),
            ("下单时间为", "orderTime"),
        ):
            if candidate.get(key) not in (None, ""):
                facts.append(f"订单 {order_id} {label} {candidate[key]}")
        if item_id:
            for label, key, suffix in (
                ("商品名称为", "productName", ""),
                ("商品规格为", "propertyInfo", ""),
                ("订单项金额", "amount", " 元"),
            ):
                if candidate.get(key) not in (None, ""):
                    facts.append(
                        f"订单项 {item_id} {label} {candidate[key]}{suffix}"
                    )
        elif candidate.get("amount") is not None:
            facts.append(f"订单 {order_id} 订单金额 {candidate['amount']} 元")
    return "。".join(facts) or None


def _capability_case_specific(clause: str) -> bool:
    return bool(
        _CASE_SPECIFIC_CAPABILITY_RE.search(clause or "")
        or _text_order_ids(clause)
        or _text_order_item_ids(clause)
    )


def _decision_applies_to_target(
    candidate: dict,
    *,
    action: str,
    order_ids: set[str],
    item_ids: set[str],
) -> bool:
    if candidate.get("action") != action:
        return False
    if order_ids and str(candidate.get("orderId") or "") not in order_ids:
        return False
    candidate_item = str(candidate.get("orderItemId") or "")
    if item_ids and candidate_item not in item_ids:
        return False
    if candidate_item and not item_ids:
        return False
    return True


def _action_capability_qualifier(text: str) -> str | None:
    if re.search(r"需要人工复核|转人工复核", text):
        return "MANUAL_REVIEW"
    if re.search(
        r"暂时无法取得|无法取得.{0,8}(?:资格|决定)|"
        r"无法给出.*结论|资格服务.*不可用",
        text,
    ):
        return "UNAVAILABLE"
    return None


def _capability_is_negative(text: str) -> bool:
    outer = _OUTER_NEGATION_RE.pattern
    negative = _NEGATIVE_CAPABILITY_RE.pattern
    positive = _POSITIVE_CAPABILITY_RE.pattern
    if re.search(rf"(?:{outer}).{{0,8}}(?:{negative})", text):
        return False
    if re.search(rf"(?:{outer}).{{0,8}}(?:{positive})", text):
        return True
    return bool(_NEGATIVE_CAPABILITY_RE.search(text))


def _capability_expected_decision(text: str) -> str:
    if (qualified := _action_capability_qualifier(text)) is not None:
        return qualified
    if _capability_is_negative(text):
        return "DENIED"
    return "ALLOWED"


def _match_assertion_text(clause: str, match: re.Match[str]) -> str:
    connectors = list(_CAPABILITY_CONNECTOR_RE.finditer(clause, 0, match.start()))
    start = connectors[-1].end() if connectors else 0
    following = _CAPABILITY_CONNECTOR_RE.search(clause, match.end())
    end = following.start() if following else len(clause)
    return clause[start:end]


def _effective_capability_assertion(
    clause: str, match: re.Match[str]
) -> str | None:
    local = _match_assertion_text(clause, match)
    if _CAPABILITY_MARKER_RE.search(local):
        return local
    return clause if _CAPABILITY_MARKER_RE.search(clause) else None


def _decision_scope_unambiguous(
    decisions: list[dict],
    *,
    action: str,
    order_ids: set[str],
    item_ids: set[str],
) -> bool:
    if len(order_ids) > 1 or len(item_ids) > 1:
        return False
    return len(
        {
            (
                str(candidate.get("orderId") or ""),
                str(candidate.get("orderItemId") or ""),
            )
            for candidate in decisions
            if candidate.get("action") == action
            and (
                not order_ids
                or str(candidate.get("orderId") or "") in order_ids
            )
            and (
                not item_ids
                or str(candidate.get("orderItemId") or "") in item_ids
            )
        }
    ) == 1


def _decision_set_supports(
    decisions: list[dict],
    *,
    action: str,
    expected_decision: str,
    order_ids: set[str],
    item_ids: set[str],
) -> bool:
    if not _decision_scope_unambiguous(
        decisions,
        action=action,
        order_ids=order_ids,
        item_ids=item_ids,
    ):
        return False
    scoped = [
        candidate
        for candidate in decisions
        if _decision_applies_to_target(
            candidate,
            action=action,
            order_ids=order_ids,
            item_ids=item_ids,
        )
    ]
    return bool(scoped) and {
        str(candidate.get("decision") or "") for candidate in scoped
    } == {expected_decision}


def _unsupported_action_capability(
    text: str, source_refs: list[dict] | dict | None
) -> str | None:
    decisions = _trusted_action_decisions(source_refs)
    pending_qualifier: str | None = None
    for clause in _assertion_clauses(text):
        qualifier = _action_capability_qualifier(clause)
        matched_any = False
        for action, pattern in _ACTION_CAPABILITY_PATTERNS.items():
            for match in pattern.finditer(clause):
                local_assertion = _effective_capability_assertion(clause, match)
                if local_assertion is None:
                    continue
                matched_any = True
                if not _capability_case_specific(clause):
                    continue
                if pending_qualifier:
                    return "回答在资格不可用或需人工复核后又声称操作可办理"
                expected = _capability_expected_decision(local_assertion)
                order_ids, item_ids = _capability_target_ids(clause, action)
                if _decision_set_supports(
                    decisions,
                    action=action,
                    expected_decision=expected,
                    order_ids=order_ids,
                    item_ids=item_ids,
                ):
                    continue
                if not decisions:
                    return (
                        f"回答声称订单具备“{action}”资格，"
                        "但缺少 Java 业务系统返回的匹配资格决定"
                    )
                return (
                    f"回答中的“{action}/{expected}”"
                    "与已核验的订单、订单项或资格决定不匹配"
                )
        if matched_any:
            pending_qualifier = None
        elif qualifier:
            pending_qualifier = qualifier
    return None


def _after_sales_qualifier(text: str) -> str | None:
    if re.search(r"需要补充|缺少.*凭证|需要证据", text):
        return "NEEDS_EVIDENCE"
    if re.search(r"无法取得|暂时无法|政策服务.*不可用", text):
        return "POLICY_UNAVAILABLE"
    return None


def _after_sales_expected_decision(text: str) -> str | None:
    if (qualified := _after_sales_qualifier(text)) is not None:
        return qualified
    if _capability_is_negative(text):
        return "INELIGIBLE"
    return "ELIGIBLE"


def _has_after_sales_capability_assertion(text: str) -> bool:
    return any(
        _CAPABILITY_MARKER_RE.search(clause)
        and any(
            pattern.search(clause)
            for pattern in _AFTER_SALES_CAPABILITY_PATTERNS.values()
        )
        for clause in _assertion_clauses(text)
    )


def _has_general_capability_claim(text: str) -> bool:
    patterns = (
        *_ACTION_CAPABILITY_PATTERNS.values(),
        *_AFTER_SALES_CAPABILITY_PATTERNS.values(),
    )
    return any(
        not _POLICY_ABSTENTION_RE.search(clause)
        and not _POLICY_UNCERTAINTY_PREFIX_RE.search(clause)
        and
        not _capability_case_specific(clause)
        and any(
            _effective_capability_assertion(clause, match) is not None
            for pattern in patterns
            for match in pattern.finditer(clause)
        )
        for clause in _assertion_clauses(text)
    )


def _unsupported_after_sales_capability(
    text: str, source_refs: list[dict] | dict | None
) -> str | None:
    decisions = _trusted_after_sales_decisions(source_refs)
    pending_qualifier: str | None = None
    for clause in _assertion_clauses(text):
        qualifier = _after_sales_qualifier(clause)
        matched_any = False
        for action, pattern in _AFTER_SALES_CAPABILITY_PATTERNS.items():
            for match in pattern.finditer(clause):
                local_assertion = _effective_capability_assertion(clause, match)
                if local_assertion is None:
                    continue
                matched_any = True
                local_start = max(0, clause.find(match.group(0)))
                local_end = local_start + len(match.group(0))
                abstentions = tuple(_POLICY_ABSTENTION_RE.finditer(clause))
                if any(
                    local_start < abstention.end()
                    and local_end > abstention.start()
                    for abstention in abstentions
                ):
                    continue
                prefix = clause[:local_start]
                uncertainty = tuple(_POLICY_UNCERTAINTY_PREFIX_RE.finditer(prefix))
                if uncertainty:
                    scope_tail = prefix[uncertainty[-1].end() :]
                    if len(scope_tail) <= 16 and not _POLICY_ADVERSATIVE_RE.search(
                        scope_tail
                    ):
                        continue
                # Generic published policy stays on the RAG gate.
                if not _capability_case_specific(clause):
                    continue
                if pending_qualifier:
                    return "回答在售后资格不可用或需证据后又声称可以办理"
                expected = _after_sales_expected_decision(local_assertion)
                if expected is None:
                    continue
                order_ids, item_ids = _capability_target_ids(clause, action)
                if _decision_set_supports(
                    decisions,
                    action=action,
                    expected_decision=expected,
                    order_ids=order_ids,
                    item_ids=item_ids,
                ):
                    continue
                if not decisions:
                    return (
                        "回答声称该订单具备售后资格，"
                        "但缺少策略引擎返回的匹配资格决定"
                    )
                return "回答中的售后资格结论与已核验的订单、订单项或策略决定不匹配"
        if matched_any:
            pending_qualifier = None
        elif qualifier:
            pending_qualifier = qualifier
    return None


def _after_sales_claim_supported(
    text: str, source_refs: list[dict] | dict | None
) -> bool:
    return _has_after_sales_capability_assertion(text or "") and (
        _unsupported_after_sales_capability(text, source_refs) is None
        and bool(_trusted_after_sales_decisions(source_refs))
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

    if _GENERAL_POLICY_RULE_RE.search(text or "") or _has_general_capability_claim(
        text or ""
    ):
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


def _unsupported_recommendation_evidence(
    candidates: list[dict], source_refs: list[dict[str, Any]]
) -> str | None:
    claims_by_product: dict[str, set[tuple[str, str]]] = {}
    for ref in source_refs:
        if (
            str(ref.get("type") or "").lower() != "product"
            or str(ref.get("source") or "")
            not in {"JAVA_GATEWAY", "JAVA_PRODUCT_SERVICE"}
            or ref.get("matched", True) is False
            or ref.get("authoritative", True) is False
        ):
            continue
        product_id = str(ref.get("productId") or ref.get("id") or "").strip()
        if not product_id:
            continue
        for claim in ref.get("claims") or []:
            if (
                not isinstance(claim, dict)
                or claim.get("claimType") != "PRODUCT_PROPERTY"
                or claim.get("subjectType") != "product"
                or str(claim.get("subjectId") or "") != product_id
                or str(claim.get("sourceId") or "") != product_id
                or str(claim.get("sourceType") or "") != str(ref.get("source") or "")
                or not str(claim.get("factPath") or "").startswith("product.property.")
            ):
                continue
            name = _normalized_scalar(claim.get("propertyName"))
            value = _normalized_scalar(claim.get("value"))
            if name and value:
                claims_by_product.setdefault(product_id, set()).add((name, value))

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        product_id = str(
            candidate.get("productId") or candidate.get("product_id") or candidate.get("id") or ""
        ).strip()
        recommendation = candidate.get("recommendation")
        evidence = (
            recommendation.get("evidence")
            if isinstance(recommendation, dict)
            else []
        )
        for item in evidence or []:
            if not isinstance(item, dict):
                return "推荐依据不是结构化的当前商品属性证据"
            evidence_product_id = str(item.get("productId") or "").strip()
            name = _normalized_scalar(item.get("propertyName"))
            value = _normalized_scalar(item.get("propertyValue"))
            if (
                not product_id
                or item.get("type") != "product_property"
                or evidence_product_id != product_id
                or not name
                or not value
                or (name, value) not in claims_by_product.get(product_id, set())
            ):
                return "推荐依据未绑定同一商品的当前 Java PRODUCT_PROPERTY claim"
    return None


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
