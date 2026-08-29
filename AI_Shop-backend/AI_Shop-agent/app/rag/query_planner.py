"""Conservative deterministic decomposition and routing for RAG v4."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.rag.canonical_facts import normalize_concept_text
from app.rag.fact_metadata import get_fact_metadata_catalog
from app.rag.query_expander import deterministic_query_variants

_DOMAIN_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("PRODUCT", ("OLED", "Mini LED", "显示技术")),
    ("CHECKOUT", ("购物车", "结算", "下单", "库存", "价格")),
    ("PAYMENT", ("支付", "付款", "支付宝", "退款进度", "比特币", "数字货币")),
    ("ACCOUNT", ("地址", "账户", "账号", "登录", "归属")),
    ("LOGISTICS", ("物流", "快递", "配送", "包裹", "收货", "发货")),
    ("AFTER_SALES", ("售后", "退货", "退款", "破损", "错发", "漏发")),
    ("PROMOTION", ("优惠券", "优惠卷", "抢券", "用券", "促销")),
    ("REVIEW", ("评价", "评论", "追评", "晒单")),
    ("PRIVACY", ("隐私", "数据导出", "删除数据", "清空聊天", "记忆")),
    ("AI_SUPPORT", ("AI助手", "AI 助手", "人工客服", "转人工", "写操作")),
    ("MEMBER", ("会员", "成长值", "签到", "等级")),
)
_BOUNDARY_RE = re.compile(r"(?:是否|能否|能不能|可不可以|支不支持|不支持|不允许|不能).{0,20}")
_MULTI_STEP_RE = re.compile(r"(?:然后|之后|再|流程|步骤|进度)")
_SPLIT_RE = re.compile(r"(?:，|；|;|并且|同时|另外|以及|还想|还要|并想)")
_EXPLICIT_TERM_RE = re.compile(r"术语\s*[“\"「『]([^”\"」』]+)[”\"」』]", re.IGNORECASE)
_EXPLICIT_FACT_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_.])([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)(?![A-Za-z0-9_.])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PlannedRagQuery:
    original_query: str
    subquestions: tuple[str, ...]
    deterministic_variants: tuple[str, ...]
    domains: tuple[str, ...]
    route: str
    expansion_reasons: tuple[str, ...]
    fact_hints: tuple[str, ...]

    def public(self, *, actual_variant_count: int, llm_expansion_calls: int) -> dict[str, Any]:
        return {
            "subquestions": list(self.subquestions),
            "domains": list(self.domains),
            "route": self.route,
            "expansionReasons": list(self.expansion_reasons),
            "factHints": list(self.fact_hints),
            "deterministicVariantCount": len(self.deterministic_variants),
            "actualVariantCount": actual_variant_count,
            "llmExpansionCalls": llm_expansion_calls,
        }


def query_domains(query: str) -> tuple[str, ...]:
    folded = str(query or "").casefold()
    return tuple(
        domain
        for domain, terms in _DOMAIN_TERMS
        if any(term.casefold() in folded for term in terms)
    )


def query_fact_hints(query: str) -> tuple[str, ...]:
    """Map explicit business propositions to canonical facts without eval labels."""

    text = str(query or "").casefold()
    hints: list[str] = []

    def add(fact_id: str) -> None:
        if fact_id not in hints:
            hints.append(fact_id)

    normalized_terms = list(
        dict.fromkeys(
            term
            for value in _EXPLICIT_TERM_RE.findall(str(query or ""))
            if (term := normalize_concept_text(value))
        )
    )
    explicit_fact_ids = {
        value.casefold() for value in _EXPLICIT_FACT_ID_RE.findall(str(query or ""))
    }
    normalized_query = normalize_concept_text(query)
    explicit_term_query = any(
        marker in normalized_query
        for marker in ("什么是", "是什么意思", "含义是什么", "定义是什么", "解释")
    )
    plain_term_matched = False
    if normalized_terms or explicit_fact_ids or explicit_term_query:
        catalog = get_fact_metadata_catalog()
        if explicit_term_query:
            for metadata in catalog.facts.values():
                for alias in metadata.aliases:
                    term = normalize_concept_text(alias)
                    if normalized_query in (
                        f"什么是{term}",
                        f"{term}是什么意思",
                        f"{term}的含义是什么",
                        f"{term}的定义是什么",
                        f"解释{term}",
                        f"解释一下{term}",
                        f"请解释{term}",
                        f"请解释一下{term}",
                    ):
                        plain_term_matched = True
                        if term not in normalized_terms:
                            normalized_terms.append(term)
        for fact_id in catalog.facts:
            if fact_id.casefold() in explicit_fact_ids:
                add(fact_id)
        for term in normalized_terms:
            matches = [
                fact_id
                for fact_id, metadata in catalog.facts.items()
                if term in {normalize_concept_text(alias) for alias in metadata.aliases}
            ]
            if len(matches) == 1:
                add(matches[0])
        if hints:
            if plain_term_matched:
                return tuple(hints)
            text = _EXPLICIT_FACT_ID_RE.sub(
                "", _EXPLICIT_TERM_RE.sub("", str(query or ""))
            ).casefold()

    price_question = any(
        term in text for term in ("价格", "成交价", "价格不同", "快照")
    )
    stock_question = any(term in text for term in ("库存", "余量", "有货"))
    checkout_price_question = price_question and any(
        term in text for term in ("提交订单", "下单时", "结算时", "结算")
    )
    if checkout_price_question:
        add(
            "checkout.price_and_stock_revalidation"
            if stock_question and any(term in text for term in ("购物车", "加入"))
            else "checkout.current_product_revalidation"
        )
    elif any(term in text for term in ("加购", "购物车")) and price_question:
        add("cart.price_snapshot_not_guarantee")
    ai_is_actor = bool(
        re.search(
            r"(?:让|由)?\s*ai(?:助手)?\s*"
            r"(?:能|会|可|可以|是否|能否|帮|直接|替|执行|退款|下单|加购|取消)",
            text,
        )
    )
    if ai_is_actor and any(
        term in text for term in ("加购", "购物车", "下单", "取消", "退款", "删除")
    ):
        add("ai.capability_and_confirmation")
    if any(term in text for term in ("会员等级", "成长值")) and any(
        term in text for term in ("门槛", "数值", "升级", "银卡", "金卡")
    ):
        add("member.growth.thresholds")
    if any(term in text for term in ("演示", "模拟")) and any(
        term in text for term in ("物流", "轨迹")
    ) and any(term in text for term in ("真实", "时效", "承诺", "sla")):
        add("logistics.simulated_no_sla")
    if any(term in text for term in ("售后资格", "退款资格", "可退资格")) and any(
        term in text for term in ("规则引擎", "知识问答", "rag")
    ):
        add("aftersales.rule_engine_authoritative")
    if "自动" in text and "收货" in text and "售后" in text:
        # This is a composite policy question: one fact explains the receipt
        # state transition and the other prevents that transition from being
        # mistaken for a universal after-sales eligibility decision.
        add("logistics.confirm_receipt")
        add("aftersales.rule_engine_authoritative")
    if "地址" in text and any(term in text for term in ("别人", "他人", "归属")) and any(
        term in text for term in ("绕过", "校验", "下单")
    ):
        add("address.ownership_check")
    if (
        any(term in text for term in ("地址簿", "地址本", "账户地址"))
        and any(term in text for term in ("已有订单", "已生成订单", "订单地址", "订单快照"))
        and any(term in text for term in ("修改", "改掉", "追改", "自动"))
    ) or (
        "订单快照" in text
        and any(term in text for term in ("地址", "履约快照"))
    ):
        add("address.order_snapshot")
    if "优惠券" in text and all(
        term in text for term in ("下单", "支付", "关闭")
    ):
        add("coupon.lock_consume_release")
    if any(
        term in text for term in ("知识库", "知识检索", "检索不足", "rag", "grounding")
    ) and any(
        term in text
        for term in (
            "证据不足",
            "证据不够",
            "找不到充分依据",
            "没有充分依据",
            "检索不足",
            "grounding",
            "证据矛盾",
            "互相矛盾",
        )
    ):
        add("rag.retrieval_and_abstention")
    if any(term in text for term in ("知识助手", "助手")) and any(
        term in text for term in ("规则不足", "依据不足", "证据不足")
    ):
        add("rag.retrieval_and_abstention")
    if any(term in text for term in ("售后", "退货", "退款")) and any(
        term in text for term in ("幂等", "重复提交", "多个申请")
    ):
        add("aftersales.submit_idempotently")
    if any(term in text for term in ("删除ai数据", "彻底删除ai数据")) and any(
        term in text for term in ("摘要", "缓存", "记忆")
    ):
        add("privacy.memory_deletion_and_withdrawal")
    if any(term in text for term in ("删除ai数据", "删除 ai 数据")) and any(
        term in text for term in ("订单", "支付", "历史", "记录")
    ):
        add("privacy.retained_business_anonymization")
    if "演示" in text and any(term in text for term in ("资金", "扣款", "扣除")):
        add("payment.demo_no_real_funds")
    if (
        any(term in text for term in ("支付失败", "付款失败", "付款页卡住", "支付页卡住"))
        and any(term in text for term in ("没有扣款", "没扣款", "未扣款", "没输入密码", "未输入密码"))
    ):
        add("payment.safe_retry_guidance")
    if "oled" in text and "mini led" in text and any(
        term in text for term in ("区别", "差别", "差异", "怎么选", "解释")
    ):
        add("product.display_technology_boundary")
    if (
        any(term in text for term in ("退货申请", "售后申请", "退货退款", "退款", "退货"))
        and any(
            term in text
            for term in (
                "订单详情",
                "入口",
                "发起",
                "申请",
                "到账",
                "支付渠道",
                "条件",
                "资格",
                "规则",
                "政策",
                "要求",
            )
        )
    ):
        add("aftersales.request_and_refund_boundary")
    if "幂等键" in text and any(
        term in text for term in ("结算内容", "请求内容", "换了", "冲突")
    ):
        add("checkout.idempotency_key")
    if any(term in text for term in ("物流", "轨迹")) and any(
        term in text for term in ("不更新", "长时间", "延迟")
    ) and any(term in text for term in ("客服", "提供", "处理")):
        add("logistics.delayed_event_support")
    if "ai" in text and "写操作" in text and any(
        term in text for term in ("确认", "执行", "立即")
    ):
        add("ai.capability_and_confirmation")
    if any(term in text for term in ("承诺", "保证")) and any(
        term in text for term in ("送达", "到达", "时效", "小时内", "sla")
    ):
        add("logistics.simulated_no_sla")
    return tuple(hints)


def plan_rag_query(query: str, *, max_subquestions: int = 3) -> PlannedRagQuery:
    original = " ".join(str(query or "").strip().split())
    raw_parts = [part.strip(" ，；;。!?！？") for part in _SPLIT_RE.split(original)]
    parts = [part for part in raw_parts if len(part) >= 4]
    part_domains = [query_domains(part) for part in parts]
    distinct_domain_parts = [
        part
        for part, domains in zip(parts, part_domains)
        if domains
    ]
    should_split = len(distinct_domain_parts) >= 2 or (
        len(parts) >= 2 and bool(_MULTI_STEP_RE.search(original))
    )
    subquestions = tuple((parts if should_split else [original])[:max_subquestions])
    domains = query_domains(original)
    reasons: list[str] = []
    if len(subquestions) > 1:
        reasons.append("multiple_business_subquestions")
    if _BOUNDARY_RE.search(original):
        reasons.append("capability_or_negative_claim")
    if _MULTI_STEP_RE.search(original):
        reasons.append("multi_step_workflow")
    deterministic: list[str] = []
    for value in (original, *subquestions):
        for variant in deterministic_query_variants(value):
            if variant and variant not in deterministic:
                deterministic.append(variant)
            if len(deterministic) == 3:
                break
        if len(deterministic) == 3:
            break
    route = "MULTI_DOMAIN" if len(domains) > 1 else domains[0] if domains else "GENERAL"
    return PlannedRagQuery(
        original_query=original,
        subquestions=subquestions or ((original,) if original else ()),
        deterministic_variants=tuple(deterministic or ([original] if original else [])),
        domains=domains,
        route=route,
        expansion_reasons=tuple(reasons),
        fact_hints=query_fact_hints(original),
    )
