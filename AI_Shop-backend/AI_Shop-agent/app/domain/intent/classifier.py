from __future__ import annotations

import asyncio
import json
import re

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from app.config.settings import get_settings
from app.domain.intent.rules import HUMAN_HINTS as _HUMAN_HINTS
from app.domain.intent.rules import deterministic_social_reply
from app.domain.intent.types import (
    IntentDecision,
    IntentKind,
    NextAction,
    RequestMode,
    RiskLevel,
    SentimentKind,
    UrgencyKind,
)
from app.harness.metrics.runtime_sensors import (
    HANDOFF_TOTAL,
    INTENT_SCHEMA_TOTAL,
    INTENT_TOTAL,
)
from app.observability.llm_metrics import invoke_llm_with_metrics
from app.services.llm_factory import create_memory_llm
from app.services.prompt_service import load_user_intent_classifier_prompt
from app.utils.order_ids import extract_order_id, extract_order_item_id
from app.utils.product_consult import is_product_consult_turn, normalize_consult_card

logger = structlog.get_logger()


async def _invoke_intent_llm(llm, messages: list, *, model: str):
    """Keep auxiliary intent classification inside its own bounded budget."""
    timeout = get_settings().intent_llm_timeout_seconds
    async with asyncio.timeout(timeout):
        return await invoke_llm_with_metrics(llm, messages, model=model)

# A5：转人工原因 label 的合法取值集合（策略分支写死的常量 + 业务侧兜底）。
# LLM 自由文本 reason 一律归一化为 OTHER，防止 Prometheus 基数膨胀。
_BOUNDED_HANDOFF_REASONS = frozenset({
    "USER_REQUEST",
    "FUND_DISPUTE",
    "SEVERE_NEGATIVE_SENTIMENT",
    "REPEATED_UNRESOLVED",
    "REPEATED_INTENT",
    "LOW_CONFIDENCE",
    "AI_HANDOFF",
})

_VERY_NEGATIVE_HINTS = (
    "诈骗",
    "骗子",
    "报警",
    "起诉",
    "曝光",
    "消费者协会",
    "12315",
    "太垃圾",
    "气死",
    "再也不买",
)
_NEGATIVE_HINTS = (
    "生气",
    "愤怒",
    "失望",
    "不满意",
    "差劲",
    "糟糕",
    "投诉",
    "没用",
    "没解决",
    "一直不",
    "怎么还",
    "烦",
)
_POSITIVE_HINTS = ("谢谢", "满意", "很好", "不错", "喜欢", "解决了")

# ---------------------------------------------------------------------------
# 支付类词汇：单一事实源
#
# 这里原先是两张表：意图分支内联一份支付词，风险判定用另一份 _FUND_RISK_HINTS。
# 两份各自增删，于是漏成了这样——
#   「同一笔订单重复支付了两次」意图表收了「重复支付」，风险表没收 → 判成 PAYMENT_ISSUE
#     但 risk=MEDIUM，不转人工，机器人自己去解释一笔重复付款；
#   「支付失败了但是钱扣了」风险表有「扣了钱」但没有「钱扣了」，词序一换就漏；
#   「订单已经取消了为什么还扣款」两张表都只有「扣款了」，连意图都没判出来，落到 CHAT/0.4。
# 而「我的钱被盗刷了」是唯一正确转人工的，原因只是「盗刷」恰好被两张表都收了。
#
# 所以问题不是"漏了几个词"，是同一件事有两处说法。改成分级派生：
#   FUND_AT_RISK    钱已经动了/该退没退 → RiskLevel.HIGH → FUND_DISPUTE 转人工
#   PAYMENT_BLOCKED 支付走不通但钱没动 → 仍是 PAYMENT_ISSUE，但不必然转人工
# 意图分支读派生出的 PAYMENT_ISSUE_HINTS，因此任何资金词都不可能只被风险表认得。
# 分级的作用：「支付失败了怎么办」这类纯操作咨询不该占用人工坐席，而任何一句提到钱
# 已经被扣走的都该走人工——这两件事必须能分开表达。
#
# 取舍：「扣款」按裸词收，不写死「重复扣款」「扣款了」这些具体说法。代价是
# 「什么时候扣款」这类售前咨询也会升级；收益是任何"钱被扣了"的说法都不会漏。
# 资金问题上多转一次人工的成本远低于漏转一次。
# ---------------------------------------------------------------------------
FUND_AT_RISK = (
    "扣款",
    "扣了钱",
    "钱扣了",
    "重复支付",
    "重复扣款",
    "扣了两次",
    "扣了两笔",
    "银行卡被扣",
    "钱没退",
    "退款没到账",
    "退款还没到账",
    "退款未到账",
    "支付成功没订单",
    "付款成功但订单没生成",
    "支付成功但订单没生成",
    "资金",
    "盗刷",
    "未授权支付",
    "不是我操作",
)
PAYMENT_BLOCKED = (
    "支付失败",
    "付款失败",
    "支付异常",
)
PAYMENT_ISSUE_HINTS = FUND_AT_RISK + PAYMENT_BLOCKED
_PAYMENT_INFORMATION_HINTS = (
    "支付方式",
    "付款方式",
    "支付渠道",
    "怎么支付",
    "如何支付",
    "支持什么支付",
)
_FUND_NEGATED_HINTS = (
    "没有扣款",
    "未扣款",
    "没扣款",
    "没有扣钱",
    "未扣钱",
    "没扣钱",
    "没有被扣",
    "扣款没有成功",
    "扣款未成功",
    "扣款没成功",
)
_FUND_INFORMATION_QUESTION_HINTS = (
    "会不会",
    "是否",
    "会否",
    "什么时候扣款",
    "何时扣款",
    "支付方式",
    "支付渠道",
    "演示支付",
    "模拟支付",
    "测试支付",
)
_FUND_INCIDENT_HINTS = (
    "已经扣",
    "实际扣",
    "被扣",
    "扣了钱",
    "钱扣了",
    "重复支付",
    "重复扣款",
    "扣了两次",
    "扣了两笔",
    "银行卡被扣",
    "钱没退",
    "退款没到账",
    "退款还没到账",
    "退款未到账",
    "支付成功没订单",
    "盗刷了",
    "被盗刷",
    "未授权支付",
    "不是我操作",
    "付款成功但订单没生成",
    "支付成功但订单没生成",
)
_UNRESOLVED_HINTS = (
    "还是没解决",
    "没有解决",
    "又不行",
    "还是不行",
    "说了没用",
    "重复问",
    "问了三次",
    "问了几次",
    "反复咨询",
    "一直没人解决",
    "一直没人处理",
)
_PRIVACY_REQUEST_HINTS = (
    "读取我的邮箱",
    "访问我的邮箱",
    "读取邮箱历史",
    "获取邮箱历史",
    "读取我的聊天记录",
    "导出我的个人信息",
    "查看我的隐私数据",
    "删除我的个人资料",
    "导出我的账户数据",
    "把聊天记录发给我",
    "查看我的个人资料",
)
_PRODUCT_ENTITY_MARKERS = (
    "手机壳", "安卓手机", "降噪耳机", "轻薄本", "手机", "耳机", "电脑", "笔记本",
    "平板", "外套", "衣服", "鞋", "零食", "饮料", "相机", "键盘", "鼠标", "音箱",
    "空气净化器", "净水器", "吉他", "玩具", "配件",
)

# Deterministic extension slots used by the customer-service contract.  These
# are deliberately bounded vocabularies/patterns: an LLM must not be allowed
# to invent a brand, feature, or compatibility target that was not present in
# the user's message.  The canonical five slots above remain unchanged for
# downstream callers; these fields only add evidence for search/consult flows.
_KNOWN_BRANDS = (
    "苹果", "华为", "索尼", "小米", "荣耀", "三星", "vivo", "OPPO", "联想", "戴尔",
    "惠普", "耐克", "阿迪达斯", "安踏", "李宁", "优衣库",
)
_KNOWN_AUDIENCES = (
    "学生", "儿童", "孩子", "老人", "男士", "女士", "上班族", "通勤族", "游戏玩家",
)
_KNOWN_OPERATING_SYSTEMS = ("安卓", "Android", "iOS", "鸿蒙", "Windows", "macOS")
_STYLE_SUFFIXES = ("款", "风格", "类型", "版", "风")
_MISSING_ITEM_MARKERS = ("少了", "缺少", "漏发", "少发", "少件")

_PRIVACY_DATA_TERMS = (
    "邮箱",
    "聊天记录",
    "个人信息",
    "个人资料",
    "账户数据",
    "账号数据",
    "隐私数据",
    "个人数据",
)
_PRIVACY_ACTION_TERMS = (
    "读取",
    "访问",
    "查看",
    "看看",
    "看一下",
    "查阅",
    "导出",
    "删除",
    "清除",
    "发给我",
    "提供",
)


def _is_privacy_request(text: str) -> bool:
    value = text or ""
    return any(term in value for term in _PRIVACY_REQUEST_HINTS) or (
        any(term in value for term in _PRIVACY_DATA_TERMS)
        and any(term in value for term in _PRIVACY_ACTION_TERMS)
    )


_ACCOUNT_SECURITY_HINTS = ("账号被盗", "账户被盗", "账号被盗了", "账户被盗了")


def _is_account_security_request(text: str) -> bool:
    return any(term in (text or "") for term in _ACCOUNT_SECURITY_HINTS)


_HUMAN_NEGATION_MARKERS = ("不要", "不用", "无需", "先别", "暂不", "暂时不", "不想")


def _has_explicit_human_request(text: str) -> bool:
    """Recognize a human request without treating negated mentions as intent."""

    for clause in re.split(r"[，,。！？!?；;\n]+", str(text or "")):
        for hint in _HUMAN_HINTS:
            start = clause.find(hint)
            while start >= 0:
                if not any(marker in clause[:start] for marker in _HUMAN_NEGATION_MARKERS):
                    return True
                start = clause.find(hint, start + len(hint))
    return False


def _extract_product_name(text: str) -> str | None:
    """Extract a conservative product span for slot evidence.

    This is a bounded entity hint, not an LLM-generated catalog item.  It only
    emits a span when a product category/model marker is present, so ordinary
    support messages do not gain a fabricated product entity.
    """

    value = str(text or "").strip()
    if not value or not (
        any(marker in value for marker in _PRODUCT_ENTITY_MARKERS)
        or re.search(r"[A-Za-z]{2,}\s*[-]?\d|\b[A-Z]{2,}\d", value)
    ):
        return None
    consult = re.search(
        r"(?:这款|这副|这个|该款|此款)\s*([^，,。！？!?；;]{1,16}?)(?=支持|有|能|可以|是否|吗|$)",
        value,
    )
    if consult:
        candidate = consult.group(1).strip()
        # Stop at comparison/attribute language. Without this boundary,
        # ``这款手机续航怎么样`` becomes the false span ``手机续航怎么样``.
        candidate = re.split(
            r"(?:支持|有|能|可以|是否|吗|怎么样|如何|哪个好|相比|对比|适配|兼容|续航|音质|价格|库存|颜色|尺寸|配置|性能|质量)",
            candidate,
            maxsplit=1,
        )[0].strip()
        candidate = re.split(r"(?:和|与|跟)\s*另一款", candidate, maxsplit=1)[0].strip()
        if any(term in value for term in ("相比", "对比", "哪个好", "比较")):
            candidate = re.split(r"\s*(?:和|与|跟)\s*", candidate, maxsplit=1)[0].strip()
        if any(marker in candidate for marker in _PRODUCT_ENTITY_MARKERS):
            return candidate
    search = re.search(
        r"(?:买|找|推荐|想要|需要)\s*(?:一款|一台|一个|个|些|点)?\s*([^，,。！？!?；;]{2,30})",
        value,
    )
    if search:
        candidate = re.split(r"预算|价格|以内|不要|不超过", search.group(1), maxsplit=1)[0]
        candidate = candidate.strip(" ：:、 ")
        if candidate and not re.fullmatch(r"[\d\s元￥¥.]+", candidate) and (
            any(marker in candidate for marker in _PRODUCT_ENTITY_MARKERS)
            or re.search(r"[A-Za-z]{2,}|\d", candidate)
        ):
            return candidate
    for marker in sorted(_PRODUCT_ENTITY_MARKERS, key=len, reverse=True):
        marker_end = value.rfind(marker)
        if marker_end >= 0:
            start_candidates = [
                value.rfind("的", 0, marker_end),
                value.rfind("、", 0, marker_end),
                value.rfind(",", 0, marker_end),
                value.rfind("，", 0, marker_end),
            ]
            start = max(start_candidates) + 1
            candidate = value[start : marker_end + len(marker)].strip(" ：:、，, ")
            candidate = re.sub(r"^(?:不要|不想要|预算[^的]*的)", "", candidate).strip()
            candidate = re.sub(
                r"^(?:这款|这副|这个|该款|此款|这台|包裹少了|少了|缺少|漏发|少发|收到的包裹少了)"
                r"\s*(?:一个|一件|一只|1个|1件|1只)?",
                "",
                candidate,
            ).strip()
            if any(term in value for term in ("相比", "对比", "哪个好", "比较")):
                candidate = re.split(r"\s*(?:和|与|跟)\s*", candidate, maxsplit=1)[0].strip()
            if candidate:
                return candidate
    return None


def _is_informational_fund_question(text: str) -> bool:
    """Distinguish payment-policy questions from an already-observed loss."""

    if not any(term in text for term in FUND_AT_RISK):
        return False
    if any(term in text for term in _FUND_INCIDENT_HINTS):
        return False
    if any(term in text for term in _FUND_INFORMATION_QUESTION_HINTS):
        return True
    return bool(
        any(term in text for term in ("扣款", "资金"))
        and text.rstrip().endswith(("吗", "么", "？", "?"))
    )


def _funds_at_risk(text: str) -> bool:
    # ``支付失败但没有扣款`` is a blocked payment, not a confirmed loss.
    # Keep the high-risk route when another confirmed incident appears in the
    # same sentence (for example, a successful payment with no order).
    if any(term in text for term in _FUND_NEGATED_HINTS) and not any(
        term in text for term in _FUND_INCIDENT_HINTS
    ):
        return False
    return any(term in text for term in FUND_AT_RISK) and not _is_informational_fund_question(text)

_TOOL_INTENTS = frozenset(
    {
        IntentKind.PRODUCT_SEARCH,
        IntentKind.VISUAL_PRODUCT_SEARCH,
        IntentKind.QUERY_ORDER,
        IntentKind.REFUND,
        IntentKind.CONFIRM_RECEIPT,
        IntentKind.QUERY_LOGISTICS,
        IntentKind.QUERY_FULFILLMENT,
        IntentKind.QUERY_COUPON,
        IntentKind.PRODUCT_REVIEW,
        IntentKind.RECOMMENT,
        IntentKind.QUERY_COMMENT,
        IntentKind.REFUND_STATUS,
        IntentKind.CANCEL_ORDER,  # 查到订单后引导用户自行取消；需要工具结果才能响应
        IntentKind.COMPLAINT,
        IntentKind.PAYMENT_ISSUE,
        IntentKind.DAMAGED_OR_WRONG_ITEM,
        IntentKind.INVOICE,
        IntentKind.ADDRESS_CHANGE,
    }
)

# The frozen conversation benchmark predates the independent support-case
# workflow.  Keep its answer/tool contract stable while the production entry
# point opts into the newer proposal flow explicitly.
_AFTER_SALES_WORKFLOW_INTENTS = frozenset(
    {
        IntentKind.COMPLAINT,
        IntentKind.PAYMENT_ISSUE,
        IntentKind.DAMAGED_OR_WRONG_ITEM,
        IntentKind.INVOICE,
        IntentKind.ADDRESS_CHANGE,
    }
)

_READ_QUERY_INTENTS = frozenset(
    {
        IntentKind.PRODUCT_CONSULT,
        IntentKind.PRODUCT_SEARCH,
        IntentKind.VISUAL_PRODUCT_SEARCH,
        IntentKind.QUERY_ORDER,
        IntentKind.QUERY_LOGISTICS,
        IntentKind.QUERY_FULFILLMENT,
        IntentKind.QUERY_COUPON,
        IntentKind.QUERY_COMMENT,
        IntentKind.REFUND_STATUS,
    }
)
_INFORMATIONAL_MARKERS = (
    "政策",
    "规则",
    "条件",
    "流程",
    "教程",
    "如何",
    "怎么",
    "怎样",
    "是否",
    "能不能",
    "能否",
    "可不可以",
    "可以吗",
    "是什么",
    "什么意思",
    "在哪",
    "哪里",
    "多久",
    "几天",
    "以后",
    "假如",
    "如果",
)
_PERSONAL_READ_MARKERS = (
    "我的订单",
    "我的退款",
    "我的物流",
    "我的优惠券",
    "我的评价",
    "这笔",
    "那笔",
    "这个订单",
    "那个订单",
    "这单",
    "那单",
    "订单号",
    "到哪了",
    "状态",
    "进度",
    "延迟",
    "没发货",
    "未发货",
    "不更新",
    "有哪些优惠券",
    "有什么优惠券",
    "优惠券有哪些",
)
_ACTION_CUES: dict[IntentKind, tuple[str, ...]] = {
    IntentKind.REFUND: (
        "我要退款", "我想退款", "帮我退款", "帮我退", "给我退", "申请退款",
        "发起退款申请", "发起退款", "办理退款", "代我退款", "替我退款",
        "直接退款", "立即退款", "退款吧", "退了吧", "退掉", "退一下",
        "继续退款",
    ),
    IntentKind.CANCEL_ORDER: (
        "我要取消", "我想取消", "帮我取消", "给我取消", "取消这个订单",
        "取消订单吧", "取消一下", "申请取消", "不要这个订单", "继续取消",
    ),
    IntentKind.CONFIRM_RECEIPT: (
        "确认收货", "已经收到", "我已收到", "收货确认", "继续确认收货",
    ),
    IntentKind.PRODUCT_REVIEW: (
        "我要评价", "我想评价", "评价一下", "提交评价", "写个评价", "给个好评",
        "给个差评", "继续评价",
    ),
    IntentKind.RECOMMENT: (
        "我要追评", "我想追评", "追评一下", "追加评价", "继续追评",
    ),
    IntentKind.ADDRESS_CHANGE: (
        "修改收货地址", "帮我改地址", "我要改地址", "换个地址", "地址填错",
    ),
    IntentKind.INVOICE: ("我要发票", "开发票", "开具发票", "申请发票", "帮我开票"),
    IntentKind.DAMAGED_OR_WRONG_ITEM: (
        "收到的商品", "我收到", "我买的", "给我处理", "帮我处理", "申请售后",
    ),
    IntentKind.AFTERSALES_UNKNOWN: ("申请售后", "帮我处理售后", "我要售后"),
    IntentKind.COMPLAINT: (
        "我要投诉", "帮我投诉", "提交投诉", "投诉商家", "帮我处理",
    ),
    IntentKind.PAYMENT_ISSUE: ("帮我处理", "提交工单", "申请处理"),
}

_ACTION_NEGATION_MARKERS = (
    "不要",
    "不用",
    "无需",
    "不需要",
    "先别",
    "暂不",
    "暂时不",
    "别再",
)

# A proposal is a write-intent boundary, even though it must not mutate state.
# Keep these cues separate from the direct-write cues: users commonly say
# "只生成提案" or "等待我确认", which contains a negation/information marker
# but still requires order resolution and a server-issued confirmation card.
_PROPOSAL_INTENTS = frozenset(
    {
        IntentKind.REFUND,
        IntentKind.CANCEL_ORDER,
        IntentKind.CONFIRM_RECEIPT,
        IntentKind.PRODUCT_REVIEW,
        IntentKind.RECOMMENT,
        IntentKind.ADDRESS_CHANGE,
        IntentKind.INVOICE,
        IntentKind.DAMAGED_OR_WRONG_ITEM,
        IntentKind.AFTERSALES_UNKNOWN,
    }
)
_PROPOSAL_MARKERS = (
    "确认卡",
    "确认提案",
    "操作确认",
    "生成提案",
    "生成方案",
    "展示方案",
    "给出确认提案",
    "等待我确认",
    "等我确认",
    "只生成",
    "仅生成",
    "只做预览",
    "仅做预览",
    "当前只做预览",
    "不要实际执行",
    "不要写入",
    "不写入",
    "保留提案",
    "只保留",
    "仅保留",
)


def _has_explicit_cancel_proposal(text: str) -> bool:
    """Recognize a cancellation proposal without delegating the boundary to the LLM.

    This deliberately requires an order id and proposal/preview wording.  A bare
    ``取消订单`` request still follows the normal order-reference flow, while a
    request such as ``只保留订单 <id> 的取消提案`` must never be downgraded to an
    informational turn merely because it mentions an unknown remote outcome.
    """

    value = str(text or "")
    if not extract_order_id(value) or "取消" not in value or "订单" not in value:
        return False
    return any(
        marker in value
        for marker in (
            "取消提案",
            "取消方案",
            "取消操作确认",
            "取消操作需要确认",
            "只保留",
            "仅保留",
            "只生成",
            "仅生成",
            "只做预览",
            "仅做预览",
            "不要实际执行",
            "不要写入",
            "不写入",
            "等待我确认",
            "等我确认",
        )
    )


def _has_non_negated_action_cue(text: str, cues: tuple[str, ...]) -> bool:
    """Recognize explicit writes while failing closed on negated requests."""

    clauses = re.split(r"[，,。！？!?；;\n]+", text)
    for clause in clauses:
        for cue in cues:
            start = clause.find(cue)
            while start >= 0:
                prefix = clause[:start]
                if not any(marker in prefix for marker in _ACTION_NEGATION_MARKERS):
                    return True
                start = clause.find(cue, start + len(cue))
    return False


def _is_direct_order_action(text: str, intent: IntentKind, asks_information: bool) -> bool:
    """Recognize terse order commands without turning questions into writes."""

    cues = {
        IntentKind.REFUND: ("退款", "退掉"),
        IntentKind.CANCEL_ORDER: ("取消订单", "取消这单", "取消该订单"),
    }.get(intent)
    if not cues or not extract_order_id(text):
        return False
    if asks_information or any(marker in text for marker in _ACTION_NEGATION_MARKERS):
        return False
    if "?" in text or "？" in text or re.search(r"(?:吗|么|嘛)\s*$", text):
        return False
    return any(cue in text for cue in cues)


def classify_request_mode(user_text: str, intent: IntentKind) -> RequestMode:
    """Deterministically separate information, reads, and proposed writes."""

    text = str(user_text or "").strip()
    if intent == IntentKind.HUMAN_REQUEST or _has_explicit_human_request(text):
        return RequestMode.HUMAN_SUPPORT

    action_cues = _ACTION_CUES.get(intent, ())
    strong_action = _has_non_negated_action_cue(text, action_cues)
    asks_information = any(marker in text for marker in _INFORMATIONAL_MARKERS)
    proposal_requested = intent in _PROPOSAL_INTENTS and any(
        marker in text for marker in _PROPOSAL_MARKERS
    )
    explicitly_delegates = any(
        marker in text for marker in ("帮我", "给我", "直接", "立即", "提交", "申请")
    )
    direct_order_action = _is_direct_order_action(text, intent, asks_information)
    if intent == IntentKind.CANCEL_ORDER and _has_explicit_cancel_proposal(text):
        return RequestMode.ACTION_PROPOSAL
    if proposal_requested or ((strong_action or direct_order_action) and (
        not asks_information or explicitly_delegates
    )):
        return RequestMode.ACTION_PROPOSAL

    # A selected order can turn the next message into an argument-only write
    # continuation.  The user normally supplies just the missing rating/content
    # instead of repeating "我要评价".  Keep this narrow and intent-bound so a
    # generic product question mentioning "五星" cannot become a write proposal.
    if (
        intent == IntentKind.PRODUCT_REVIEW
        and not asks_information
        and re.search(r"(?:[1-5一二三四五]|[壹贰叁肆伍])\s*(?:星|分)", text)
    ):
        return RequestMode.ACTION_PROPOSAL
    if (
        intent == IntentKind.RECOMMENT
        and not asks_information
        and any(marker in text for marker in ("补充", "追加", "追评"))
    ):
        return RequestMode.ACTION_PROPOSAL

    personal_read = any(marker in text for marker in _PERSONAL_READ_MARKERS)
    if personal_read:
        return RequestMode.READ_QUERY
    if asks_information:
        return RequestMode.INFORMATIONAL
    if intent in _READ_QUERY_INTENTS:
        return RequestMode.READ_QUERY
    return RequestMode.INFORMATIONAL


def _structural_intent(
    user_text: str,
    *,
    from_product: bool = False,
    consult_card: dict | None = None,
    message_card: dict | None = None,
) -> IntentKind | None:
    if _has_order_action_cue(user_text):
        return None
    if is_product_consult_turn(
        user_text, message_card, consult_card, from_product=from_product
    ):
        return IntentKind.PRODUCT_CONSULT
    return None


def _has_order_action_cue(text: str) -> bool:
    value = text or ""
    return any(
        hint in value
        for hint in (
            "退款", "退货", "退钱", "取消", "确认收货", "物流", "快递",
            "发货了吗", "没发货", "未发货", "催发货", "评价", "好评", "差评", "五星", "追评",
            "发票", "改地址", "修改地址", "破损", "损坏", "坏了", "错发", "漏发",
        )
    )


def classify_intent_by_rules(
    user_text: str,
    *,
    from_product: bool = False,
    consult_card: dict | None = None,
    message_card: dict | None = None,
    session_intent: str | None = None,
) -> IntentKind | None:
    from app.domain.intent.rules import (
        looks_like_category_switch,
        looks_like_intent_continuation,
        looks_like_new_product_search,
    )
    from app.services.product_service import is_similar_or_recommend_request

    text = (user_text or "").strip()
    lower = text.lower()
    if not text:
        return IntentKind.CHAT

    structural = _structural_intent(
        text,
        from_product=from_product,
        consult_card=consult_card,
        message_card=message_card,
    )
    if structural:
        return structural

    if _has_explicit_human_request(text):
        return IntentKind.HUMAN_REQUEST
    # Privacy/authority requests are safety-sensitive even when the user does
    # not literally say "转人工".  Route them to the human-support boundary
    # before generic chat or product rules can consume the turn.
    if _is_privacy_request(text):
        return IntentKind.HUMAN_REQUEST
    if _is_account_security_request(text):
        return IntentKind.HUMAN_REQUEST
    # refund-007：进度问法只写死了「退款到账」，补上带时间词的问法——
    # 「退款要多久到账」之前被后面的「退款」泛匹配抢走判成 REFUND。
    if any(k in text for k in (
        "退款进度", "退款到哪", "退款到账", "退款什么时候", "退款状态",
        "退款还没到账", "退款未到账",
        "多久到账", "几天到账", "何时到账", "什么时候到账", "多久退", "几天退",
    )):
        personal_refund = any(
            k in text
            for k in ("我的退款", "这笔退款", "那笔退款", "退款单", "我退的", "给我退")
        ) or bool(extract_order_id(text) or extract_order_item_id(text)) or any(
            k in text for k in ("退款还没到账", "退款未到账")
        )
        if personal_refund:
            return IntentKind.REFUND_STATUS
        # Without an order/started-refund context this is a policy/timing
        # question.  Keep it on the REFUND knowledge path; REFUND_STATUS is
        # reserved for user-specific progress lookup.
        return IntentKind.REFUND
    if _is_informational_fund_question(text):
        return IntentKind.CHAT
    if any(k in text for k in PAYMENT_ISSUE_HINTS):
        return IntentKind.PAYMENT_ISSUE
    if any(
        k in text
        for k in (
            "破损",
            "损坏",
            "坏了",
            "坏的",
            "碎了",
            "错发",
            "发错",
            "漏发",
            "少发",
            "少了",
            "缺件",
            "缺少",
            "配件不全",
            "错的颜色",
            "颜色不对",
            "质量问题",
            "想换货",
            "换货",
            "假货",
        )
    ):
        return IntentKind.DAMAGED_OR_WRONG_ITEM

    if any(term in text for term in _UNRESOLVED_HINTS) and any(
        marker in text for marker in ("问题", "处理", "客服", "投诉", "解决")
    ):
        return IntentKind.COMPLAINT

    # Product search constraints must win over the generic consultation
    # question route. ``有没有适合学生的平板，预算 2000`` is a search request,
    # while ``耳机有主动降噪吗`` remains a product consultation.
    if (
        not _has_order_action_cue(text)
        and any(marker in text for marker in _PRODUCT_ENTITY_MARKERS)
        and any(
        marker in text
        for marker in (
            "适合",
            "预算",
            "不超过",
            "以内",
            "不要",
            "不想要",
            "不含",
            "无糖",
            "少糖",
            "推荐",
            "买",
            "找",
            "搜索",
            "怎么选",
            "如何选",
            "哪个好",
            "对比",
            "比较",
        )
        )
    ):
        return IntentKind.PRODUCT_SEARCH

    # A specification question mentioning a product is consultation, not a
    # fresh search.  This must run before the short-text product keyword rule
    # ("耳机" alone is otherwise a direct search keyword).
    from app.domain.intent.rules import looks_like_consult_question

    if (
        (
            looks_like_consult_question(text)
            or re.search(r"(?:有|支持|适配|兼容).*(?:吗|么|嘛|呢|？|\?)$", text)
        )
        and not _has_order_action_cue(text)
        and any(marker in text for marker in _PRODUCT_ENTITY_MARKERS)
        and not any(verb in text for verb in ("买", "找", "推荐", "搜索"))
        and not any(marker in text for marker in ("怎么选", "如何选", "哪个好", "对比", "比较"))
    ):
        return IntentKind.PRODUCT_CONSULT

    if (
        not any(k in text for k in (
            "退款", "退货", "退钱", "取消", "确认收货", "评价", "追评",
            "地址", "发票", "物流", "快递", "包裹", "运单",
        ))
        and any(k in text for k in ("催发货", "催一下发货", "发货了吗", "发货了没", "怎么还没发货", "怎么还不发货", "还没发货", "没发货", "未发货"))
    ):
        if not any(k in text for k in ("一般多久发货", "通常多久发货", "多久能发货", "什么时候能发货")):
            return IntentKind.QUERY_FULFILLMENT

    # logi-006：物流异常问法（「物流一直不动怎么办」）要的是轨迹而不是操作说明，
    # 必须抢在 howto 分支之前——否则「怎么」+「物流」会先命中 howto 判成 CHAT。
    if any(k in text for k in ("物流", "快递", "包裹", "运单")) and any(
        k in text
        for k in ("不动", "没动", "没动静", "卡住", "停滞", "不更新", "一直不",
                  "怎么还不", "异常", "没派送", "没到", "丢件", "丢失", "不见了")
    ):
        return IntentKind.QUERY_LOGISTICS

    # 操作方法/如何/怎么类 → CHAT（必须在 INVOICE/ADDRESS_CHANGE 等专项分支之前执行，
    # 否则「发票怎么申请」「确认收货在哪里点」会被专项分支抢走，导致错误路由或触发
    # PROPOSE_CONFIRM_RECEIPT 等副作用）。
    howto = any(
        k in text
        for k in (
            "如何",
            "怎么",
            "怎样",
            "步骤",
            "方法",
            "教程",
            "在哪用",
            "哪里用",
            "怎么用",
            "如何用",
            "怎么使用",
            "如何使用",
            "在哪使用",
            "在哪里用",
            "在哪里",  # 覆盖「确认收货在哪里点」「在哪里取消」等格式
            "在哪看",
            "哪里看",
        )
    )
    if howto and any(
        k in text
        for k in (
            "取消",
            "优惠券",
            "优惠卷",
            "用券",
            "退款",
            "退货",
            "退",     # refund-006：「七天无理由怎么退」只含单字「退」
            "评价",
            "追评",   # 「追评怎么写」
            "收货",
            "物流",
            "快递",
            "订单",
            "发票",
            "地址",
        )
    ):
        return IntentKind.CHAT

    if any(k in text for k in ("开发票", "发票", "抬头", "税号")):
        return IntentKind.INVOICE
    if any(k in text for k in ("修改地址", "改地址", "收货地址错", "地址填错", "换地址")) or (
        "地址" in text and any(k in text for k in ("改", "修改", "换"))
    ):
        return IntentKind.ADDRESS_CHANGE
    if "投诉" in text or any(k in text for k in _VERY_NEGATIVE_HINTS):
        return IntentKind.COMPLAINT

    if any(k in text for k in ("追评", "再评", "二次评价")):
        return IntentKind.RECOMMENT
    # 订单已在上一轮被确定性定位后，用户通常只补充“五星，音质很好”，
    # 不会再次复述“我要评价”。评分表达是写提案的必要参数，因此只在上一轮
    # 明确处于评价流程时延续，避免把普通的“五分”商品咨询误判成写操作。
    if session_intent == IntentKind.PRODUCT_REVIEW.value and re.search(
        r"(?:[1-5一二三四五]|[壹贰叁肆伍])\s*(?:星|分)", text
    ):
        return IntentKind.PRODUCT_REVIEW
    if (
        session_intent == IntentKind.RECOMMENT.value
        and len(text) <= 120
        and any(
            hint in text
            for hint in (
                "音质", "降噪", "做工", "质量", "续航", "手感", "包装",
                "物流", "客服", "好用", "满意", "不错", "很好", "一般",
                "失望", "差", "补充", "追加",
            )
        )
    ):
        return IntentKind.RECOMMENT
    # refund-005：「这东西我不想要了，退了吧」之前匹配不到「退款/退货/退钱」。
    # refund-008 过宽修复：「要退」「想退」是子串匹配，政策/疑问问法（"要不要退
    # 差价""要退运费吗""想退就退"）会被误判成退款动作；排除疑问/假设式后只认
    # 明确的退款意向。
    if any(k in text for k in ("退款", "退货", "退钱", "退了吧", "退掉", "退一下", "给我退", "帮我退", "申请退")) or (
        any(k in text for k in ("想退", "要退"))
        # 排除假设/疑问式政策问法（"要不要退差价""想退就退""能退吗"），
        # 以及句首无主语的"要退运费吗"；带主语的"我想退，可以吗"仍算退款。
        and not any(k in text for k in ("要不要", "想退就", "能退", "可以退", "给退"))
        and not re.match(r"^要退", text)
    ):
        return IntentKind.REFUND
    if any(k in text for k in ("确认收货", "已收到", "收货确认")):
        return IntentKind.CONFIRM_RECEIPT
    # cancel-002：「这个订单不要了，取消」——「取消」和「订单」都在但原表里的
    # 固定短语一个都匹配不上，补一条组合判断（howto 分支在前，政策问法不受影响）。
    # 过宽修复（P1 审查）：组合判断不区分语态，会把查询型问法（"我的订单被取消了
    # 吗""取消的订单去哪了"）误判成取消动作并触发强制取消引导文案；排除被动/
    # 过去式/疑问式后只认"主动取消"语义。
    if any(k in text for k in (
        "取消这个订单", "不要这个订单", "帮我取消", "给我取消", "取消订单",
        "取消掉", "取消吧", "取消一下", "想取消", "要取消", "申请取消",
    )) or (
        "取消" in text
        and "订单" in text
        and not any(k in text for k in (
            "被取消", "已取消", "取消了", "取消的", "是不是", "为什么", "为何",
            "什么原因", "怎么回事",
        ))
    ):
        return IntentKind.CANCEL_ORDER
    if any(k in text for k in ("物流", "快递", "到哪了", "运单", "包裹")):
        return IntentKind.QUERY_LOGISTICS
    if any(k in text for k in ("查看评价", "评价内容", "写了什么评价", "我的评价")):
        return IntentKind.QUERY_COMMENT
    if "评价" in text and any(k in text for k in ("查一下", "查查", "看看", "那单", "这单")):
        return IntentKind.QUERY_COMMENT
    # review-004：只说「我要评价」也要认出评价意图，再由工具层追问单号，
    # 不能落成 CHAT + 建议转人工。
    if any(k in text for k in (
        "我要评价", "我想评价", "想评价", "去评价", "来评价", "评价一下",
        "想写评价", "写个评价", "给我评价",
    )):
        return IntentKind.PRODUCT_REVIEW
    # review-003：「打3分」中间夹数字，原表里「打分」匹配不上；补正则。
    if (
        any(k in text for k in ("评价", "好评", "差评", "打分", "评星", "星级"))
        or re.search(r"打\s*\d+\s*分", text)
    ) and any(
        k in text for k in ("订单", "给", "写", "提交")
    ):
        return IntentKind.PRODUCT_REVIEW
    if any(k in text for k in (
        "我的优惠券", "查优惠券", "有哪些券", "有哪些优惠券", "有什么优惠券",
        "优惠券有哪些", "还有几张券", "可用券", "未使用券", "这张券", "那张券",
    )):
        return IntentKind.QUERY_COUPON
    # 含「订单」的复合句（如「帮我看看订单，顺便查一下优惠券」）主意图是 QUERY_ORDER，
    # 不应被「优惠券 + 看看」触发的宽泛规则抢走。
    if (
        any(k in text for k in ("优惠券", "优惠卷"))
        and any(k in text for k in ("查", "看看", "有没有", "还有", "几张", "列表"))
        and "订单" not in text
    ):
        return IntentKind.QUERY_COUPON
    if "取消" not in text and any(
        k in text
        for k in (
            "我的订单",
            "查订单",
            "买了什么",
            "买过什么",
            "最近买",
            "最近购买",
            "最近订单",
            "订单列表",
            "上次买",
            "再买一次",
            "复购",
        )
    ):
        return IntentKind.QUERY_ORDER

    if is_similar_or_recommend_request(text) or looks_like_new_product_search(text):
        return IntentKind.PRODUCT_SEARCH

    if re.search(r"商品(?:ID|id)?[：:\s]*[A-Za-z0-9_-]{3,32}", text) and any(
        marker in text for marker in ("详情", "参数", "介绍", "查")
    ):
        return IntentKind.PRODUCT_CONSULT

    consult_name = (consult_card or {}).get("productName") or (consult_card or {}).get(
        "product_name"
    )
    if consult_card and looks_like_category_switch(text, consult_name):
        return IntentKind.PRODUCT_SEARCH
    if any(k in text for k in ("搜索", "找", "买", "推荐", "热销", "爆款")) and len(text) <= 40:
        return IntentKind.PRODUCT_SEARCH
    if any(k in text for k in ("售后", "退换", "商品有问题", "订单有问题")):
        return IntentKind.AFTERSALES_UNKNOWN
    # A2 会话级意图延续：所有显式分支都没命中、文本又短又像"上一轮话题的延续"时，
    # 沿用上一轮意图而不是落到 default CHAT（行业共识：意图不每轮重猜）。
    # 放最后是因为它只负责"延续"，绝不能抢走任何带新信息的问法。
    if looks_like_intent_continuation(text, session_intent):
        try:
            return IntentKind(session_intent)
        except ValueError:
            return None
    if lower in {
        "你好",
        "您好",
        "hello",
        "hi",
        "在吗",
        "谢谢",
        "谢谢你",
        "感谢",
        "多谢",
    }:
        return IntentKind.CHAT
    return None


def classify_high_confidence_order_intent(user_text: str) -> IntentKind | None:
    intent, _ = classify_high_confidence_intent(user_text)
    return intent


def classify_high_confidence_intent(
    user_text: str, session_intent: str | None = None
) -> tuple[IntentKind | None, str]:
    text = (user_text or "").strip()
    if not text:
        return None, ""
    order_id = extract_order_id(text) or ""

    ruled = classify_intent_by_rules(text, session_intent=session_intent)
    # An explicit order id plus cancellation-proposal wording is a closed,
    # safety-sensitive contract.  Resolve it before the auxiliary LLM so model
    # variance cannot turn a write proposal into QUERY_ORDER/CHAT.
    if ruled == IntentKind.CANCEL_ORDER and _has_explicit_cancel_proposal(text):
        return IntentKind.CANCEL_ORDER, order_id
    if ruled in {
        IntentKind.HUMAN_REQUEST,
        IntentKind.COMPLAINT,
        IntentKind.PAYMENT_ISSUE,
        IntentKind.DAMAGED_OR_WRONG_ITEM,
        IntentKind.REFUND_STATUS,
        IntentKind.INVOICE,
        IntentKind.ADDRESS_CHANGE,
        IntentKind.QUERY_FULFILLMENT,
    }:
        return ruled, order_id
    if order_id and any(
        k in text for k in ("到哪里", "到哪了", "物流", "快递", "运单", "包裹", "轨迹")
    ):
        return IntentKind.QUERY_LOGISTICS, order_id
    # order-006：带单号问「现在什么状态/进展到哪了」是订单查询的常见问法。
    if order_id and any(
        k in text for k in ("状态", "进展", "情况", "怎么样了", "什么情况", "到哪一步")
    ):
        # 修复（P1 审查）：带退款词的单号（"退款单号XXX现在什么情况"）问的是
        # 退款进度，应归 REFUND_STATUS 而不是 QUERY_ORDER。
        if any(k in text for k in ("退款", "退货", "退钱", "退款单", "退货单")):
            return IntentKind.REFUND_STATUS, order_id
        return IntentKind.QUERY_ORDER, order_id
    if any(
        k in text
        for k in (
            "我的订单",
            "最近的订单",
            "最近订单",
            "查订单",
            "订单列表",
            "买了什么",
            "买过什么",
            "最近买了",
            "最近买的",
            "最近购买",
            "上次买",
            "再买一次",
            "复购",
        )
    ):
        return IntentKind.QUERY_ORDER, order_id
    return None, ""


def analyze_sentiment(user_text: str) -> SentimentKind:
    text = (user_text or "").strip()
    if any(k in text for k in _VERY_NEGATIVE_HINTS):
        return SentimentKind.VERY_NEGATIVE
    negative_hits = sum(1 for k in _NEGATIVE_HINTS if k in text)
    if negative_hits >= 2 or ("!" in text and negative_hits):
        return SentimentKind.VERY_NEGATIVE
    if negative_hits:
        return SentimentKind.NEGATIVE
    if any(k in text for k in _POSITIVE_HINTS):
        return SentimentKind.POSITIVE
    return SentimentKind.NEUTRAL


def _normalize_budget_span(number: str, unit: str | None, qualifier: str | None) -> str:
    """Keep a human-readable budget span without changing numeric ``amount``."""

    value = re.sub(r"\s+", "", str(number or ""))
    currency = str(unit or "元").strip() or "元"
    suffix = str(qualifier or "").strip()
    return f"{value}{currency}{suffix}"


def _extract_budget(text: str) -> str | None:
    # Match both ``预算 2000 元`` and ``500 元以内``.  The qualifier is part of
    # the semantic slot, while ``amount`` continues to expose only the number.
    patterns = (
        r"(?:预算|预算为|预算是|价格不超过|不超过|不高于)\s*[¥￥]?\s*"
        r"(\d+(?:\.\d{1,2})?)\s*(元|块)?\s*(以内|以下|之内)?",
        r"[¥￥]?\s*(\d+(?:\.\d{1,2})?)\s*(元|块)\s*(以内|以下|之内)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _normalize_budget_span(match.group(1), match.group(2), match.group(3))
    return None


def _extract_extended_entities(text: str) -> dict[str, str]:
    """Extract bounded customer-service slots from the original user span."""

    entities: dict[str, str] = {}
    budget = _extract_budget(text)
    if budget:
        entities["budget"] = budget

    # Explicit exclusions must be classified before positive brand extraction,
    # otherwise ``不要苹果，推荐安卓手机`` would expose ``brand=苹果`` too.
    excluded_brands: set[str] = set()
    for brand in _KNOWN_BRANDS:
        if re.search(
            rf"(?:不要|不选|排除|不考虑)\s*(?:品牌\s*)?{re.escape(brand)}",
            text,
            flags=re.IGNORECASE,
        ):
            excluded_brands.add(brand)
    if excluded_brands:
        entities["excludedBrand"] = next(
            brand for brand in _KNOWN_BRANDS if brand in excluded_brands
        )

    for brand in _KNOWN_BRANDS:
        if brand in excluded_brands:
            continue
        if brand in text and any(marker in text for marker in ("买", "找", "推荐", "手机", "耳机", "电脑")):
            entities["brand"] = brand
            break

    for system in _KNOWN_OPERATING_SYSTEMS:
        if re.search(rf"(?<![A-Za-z]){re.escape(system)}(?![A-Za-z])", text, flags=re.IGNORECASE):
            entities["operatingSystem"] = system
            break

    bluetooth = re.search(
        r"蓝牙\s*(?:版本|version|v)?\s*([0-9]+(?:\.[0-9]+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if bluetooth:
        entities["bluetoothVersion"] = bluetooth.group(1)

    # Prefer the more specific phrase first so ``主动降噪`` is not shortened to
    # ``降噪``.  Only phrases asked about in the message are emitted.
    for feature in ("主动降噪", "降噪", "续航"):
        if feature in text:
            entities["feature"] = feature
            break

    for audience in _KNOWN_AUDIENCES:
        if re.search(rf"适合\s*{re.escape(audience)}(?:的|用|人群|$)", text):
            entities["audience"] = audience
            break

    exclusion = re.search(
        r"(?:不要|不想要|排除|不考虑)\s*([^，,。！？!?；;]+)",
        text,
    )
    if exclusion:
        candidate = exclusion.group(1).strip()
        if candidate and not any(brand in candidate for brand in _KNOWN_BRANDS):
            # A style slot is emitted only for an explicit style/type noun.  We
            # intentionally do not generalize every negated phrase into a slot.
            style = re.search(
                rf"([^\s，,。！？!?；;]+(?:{'|'.join(map(re.escape, _STYLE_SUFFIXES))}))",
                candidate,
            )
            if style:
                entities["excludedStyle"] = style.group(1)

    missing_item = re.search(
        r"(?:少了|缺少|漏发|少发)\s*(一件|一个|一只|1件|1个|1只|\d+件|\d+个|\d+只)?\s*配件",
        text,
    )
    if missing_item:
        quantity = missing_item.group(1) or "1"
        quantity = {"一件": "1", "一个": "1", "一只": "1"}.get(quantity, quantity)
        quantity = re.sub(r"(?:件|个|只)$", "", quantity)
        entities["quantity"] = quantity or "1"

    compatible = re.search(
        r"(?:适配|兼容|适用于|支持)\s*"
        r"([A-Za-z][A-Za-z0-9-]*(?:\s+[A-Za-z0-9-]+)*\s*\d+)",
        text,
        flags=re.IGNORECASE,
    )
    if compatible:
        entities["compatibleModel"] = re.sub(r"\s+", " ", compatible.group(1).strip())
    return entities


def extract_entities(user_text: str, data: str = "") -> dict[str, str]:
    text = user_text or ""
    entities: dict[str, str] = {}
    order_item_id = extract_order_item_id(text, data)
    order_id = extract_order_id(text, data)
    if order_item_id:
        entities["orderItemId"] = order_item_id
    if order_id:
        entities["orderId"] = order_id
    amount = re.search(
        r"(?:[¥￥]\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块)?|"
        r"(\d+(?:\.\d{1,2})?)\s*(?:元|块))",
        text,
    )
    if amount:
        entities["amount"] = amount.group(1) or amount.group(2)
    product_id = re.search(r"(?:商品(?:ID|id)?)[：:\s]*([A-Za-z0-9_-]{3,32})", text)
    if product_id:
        entities["productId"] = product_id.group(1)
    product_name = _extract_product_name(text)
    if product_name:
        entities["productName"] = product_name
    entities.update(_extract_extended_entities(text))
    return entities


def _parse_intent_json(raw: str) -> dict | None:
    text = (raw or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _build_intent_context(
    user_id: str,
    user_text: str,
    *,
    from_product: bool = False,
    consult_card: dict | None = None,
    message_card: dict | None = None,
) -> str:
    consult = normalize_consult_card(consult_card) or normalize_consult_card(message_card)
    lines = [
        "=== 当前上下文 ===",
        f"商品详情通道(fromProduct)：{'是' if from_product else '否'}",
        f"消息含商品卡片：{'是' if normalize_consult_card(message_card) else '否'}",
    ]
    if consult:
        lines.append(
            f"咨询中商品：{consult.get('productName')}（ID={consult.get('productId')}）"
        )
    else:
        lines.append("咨询中商品：无")
    if extract_order_id(user_text or ""):
        lines.append("用户消息含订单号样式文本：是")
    return "\n".join(lines)


def _use_structured_intent_output() -> bool:
    """Return whether the configured intent provider supports JSON schema.

    The explicit setting is the source of truth. ``auto`` retains the
    provider-native path for normal OpenAI-compatible clients while avoiding a
    known incompatibility in DeepSeek v4-flash, whose endpoint rejects the
    JSON-schema response format with a 400. The text-JSON path below remains
    bounded and schema-validated.
    """

    settings = get_settings()
    mode = settings.intent_structured_output_mode
    if mode == "enabled":
        return True
    if mode == "disabled":
        return False
    model = (settings.memory_llm_model or settings.llm_model).strip().lower()
    return model not in {"deepseek-v4-flash", "deepseek-v4-flash-latest"}


async def classify_intent_by_llm(
    user_id: str,
    user_text: str,
    *,
    from_product: bool = False,
    consult_card: dict | None = None,
    message_card: dict | None = None,
) -> IntentDecision | None:
    settings = get_settings()
    if not (settings.memory_llm_api_key or settings.llm_api_key).strip():
        INTENT_SCHEMA_TOTAL.labels(result="unavailable").inc()
        logger.debug(
            "intent_llm_skipped",
            reason="memory_llm_api_key_not_configured",
        )
        return _build_decision(
            IntentKind.CHAT,
            user_text,
            confidence=0.0,
            source="llm_unavailable",
            next_action=NextAction.ASK_CLARIFICATION,
        )

    template = await load_user_intent_classifier_prompt()
    if not template.strip():
        return None

    context = _build_intent_context(
        user_id,
        user_text,
        from_product=from_product,
        consult_card=consult_card,
        message_card=message_card,
    )
    try:
        base = template % (user_id, user_text)
    except TypeError:
        base = f"{template}\n\n用户ID：{user_id}\n用户问题：{user_text}"

    prompt = f"{base}\n\n{context}"
    messages = [
        SystemMessage(
            content=(
                "你是电商客服意图分类器。严格按提供的 IntentDecision schema 返回；"
                "不要解释，不要执行任何业务操作。"
            )
        ),
        HumanMessage(content=prompt),
    ]
    try:
        # Intent classification is bounded schema extraction, not open-ended
        # reasoning.  Disabling thinking removes an avoidable latency/token
        # tail while the existing schema validation and safe fallback remain.
        llm = create_memory_llm(disable_thinking=True)
    except Exception as exc:
        INTENT_SCHEMA_TOTAL.labels(result="invalid").inc()
        logger.warning(
            "intent_llm_create_failed_safe_fallback",
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        return _build_decision(
            IntentKind.CHAT,
            user_text,
            confidence=0.0,
            source="llm_invalid",
            next_action=NextAction.ASK_CLARIFICATION,
        )

    structured_error: Exception | None = None
    if _use_structured_intent_output():
        try:
            structured_llm = llm.with_structured_output(IntentDecision, include_raw=True)
            response = await _invoke_intent_llm(
                structured_llm,
                messages,
                model=get_settings().memory_llm_model or get_settings().llm_model,
            )
            parsed = response.get("parsed") if isinstance(response, dict) else response
            parsing_error = response.get("parsing_error") if isinstance(response, dict) else None
            if parsing_error is not None:
                raise ValueError(f"structured intent parsing failed: {parsing_error}")
            decision = IntentDecision.model_validate(parsed)
            INTENT_SCHEMA_TOTAL.labels(result="schema_success").inc()
            return _normalize_llm_decision(decision, user_text, source="llm_structured")
        except Exception as exc:
            structured_error = exc
            logger.info(
                "intent_structured_output_fallback",
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )
    else:
        logger.info(
            "intent_structured_output_skipped",
            reason="provider_capability_or_configuration",
            model=get_settings().memory_llm_model or get_settings().llm_model,
        )

    try:
        response = await _invoke_intent_llm(
            llm,
            [
                SystemMessage(
                    content=(
                        "你是电商客服意图分类器。只输出一行 JSON，字段为："
                        "intentType、confidence、data、entities、sentiment、urgency、"
                        "riskLevel、nextAction、handoffReason。禁止解释、禁止 markdown。"
                    )
                ),
                HumanMessage(content=prompt),
            ],
            model=get_settings().memory_llm_model or get_settings().llm_model,
        )
        content = (
            response.content
            if isinstance(response.content, str)
            else str(response.content or "")
        )
        obj = _parse_intent_json(content)
        if not obj:
            logger.warning("intent_llm_parse_failed", raw=content[:200])
            raise ValueError("intent text fallback did not return JSON")
        decision = _decision_from_text_json(obj, user_text)
        INTENT_SCHEMA_TOTAL.labels(result="fallback").inc()
        return decision
    except Exception as exc:
        INTENT_SCHEMA_TOTAL.labels(result="invalid").inc()
        logger.warning(
            "intent_llm_invalid_safe_fallback",
            structured_error_type=(type(structured_error).__name__ if structured_error else None),
            fallback_error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        return _build_decision(
            IntentKind.CHAT,
            user_text,
            confidence=0.0,
            source="llm_invalid",
            next_action=NextAction.ASK_CLARIFICATION,
        )


def _normalize_llm_decision(
    decision: IntentDecision, user_text: str, *, source: str
) -> IntentDecision:
    data = str(decision.data or "").strip()
    entities = {
        str(key): str(value)
        for key, value in (decision.entities or {}).items()
        if value not in (None, "")
    }
    return decision.model_copy(
        update={
            "confidence": _clamp_confidence(decision.confidence, 0.75),
            "entities": {**extract_entities(user_text, data), **entities},
            "source": source,
            "data": data,
        }
    )


def _decision_from_text_json(
    obj: dict[str, object], user_text: str
) -> IntentDecision:
    try:
        intent = IntentKind(
            str(obj.get("intentType") or obj.get("intent_type") or "").upper()
        )
    except ValueError as exc:
        raise ValueError("unknown intent in text fallback") from exc
    data = str(obj.get("data") or obj.get("keyword") or "").strip()
    raw_entities = obj.get("entities") if isinstance(obj.get("entities"), dict) else {}
    entities = {
        str(key): str(value)
        for key, value in raw_entities.items()
        if value not in (None, "")
    }
    return IntentDecision(
        intent=intent,
        confidence=_clamp_confidence(obj.get("confidence"), 0.75),
        entities={**extract_entities(user_text, data), **entities},
        sentiment=_enum_or_default(
            SentimentKind, obj.get("sentiment"), analyze_sentiment(user_text)
        ),
        urgency=_enum_or_default(UrgencyKind, obj.get("urgency"), UrgencyKind.NORMAL),
        risk_level=_enum_or_default(
            RiskLevel, obj.get("riskLevel") or obj.get("risk_level"), RiskLevel.LOW
        ),
        next_action=_enum_or_default(
            NextAction, obj.get("nextAction") or obj.get("next_action"), NextAction.ANSWER
        ),
        handoff_reason=str(obj.get("handoffReason") or "").strip() or None,
        source="llm_fallback",
        data=data,
    )


async def resolve_intent(
    user_id: str,
    user_text: str,
    *,
    from_product: bool = False,
    consult_card: dict | None = None,
    message_card: dict | None = None,
    unresolved_count: int = 0,
    allow_llm: bool = True,
    session_intent: str | None = None,
    recent_intents: list[str] | None = None,
    record_metrics: bool = True,
    after_sales_workflow: bool = False,
) -> IntentDecision:
    structural = _structural_intent(
        user_text,
        from_product=from_product,
        consult_card=consult_card,
        message_card=message_card,
    )
    if structural is not None:
        decision = _build_decision(
            structural,
            user_text,
            confidence=0.99,
            source="structural",
            after_sales_workflow=after_sales_workflow,
        )
        return _record_and_apply(
            decision,
            user_text,
            unresolved_count,
            recent_intents=recent_intents,
            record_metrics=record_metrics,
            after_sales_workflow=after_sales_workflow,
        )

    if deterministic_social_reply(user_text) is not None:
        decision = _build_decision(
            IntentKind.CHAT,
            user_text,
            confidence=0.99,
            source="deterministic_social",
            next_action=NextAction.ANSWER,
            after_sales_workflow=after_sales_workflow,
        )
        return _record_and_apply(
            decision,
            user_text,
            unresolved_count,
            recent_intents=recent_intents,
            record_metrics=record_metrics,
            after_sales_workflow=after_sales_workflow,
        )

    high_intent, high_data = classify_high_confidence_intent(
        user_text, session_intent=session_intent
    )
    if high_intent is not None:
        decision = _build_decision(
            high_intent,
            user_text,
            confidence=0.96,
            source="rule_priority",
            data=high_data,
            after_sales_workflow=after_sales_workflow,
        )
        return _record_and_apply(
            decision,
            user_text,
            unresolved_count,
            recent_intents=recent_intents,
            record_metrics=record_metrics,
            after_sales_workflow=after_sales_workflow,
        )

    settings = get_settings()
    if allow_llm and settings.intent_use_llm:
        llm_decision = await classify_intent_by_llm(
            user_id,
            user_text,
            from_product=from_product,
            consult_card=consult_card,
            message_card=message_card,
        )
        # An auxiliary provider failure is not a business decision. Continue to
        # deterministic rules so a safe, high-confidence request (for example
        # product search) is not silently downgraded to generic CHAT.
        if llm_decision is not None and llm_decision.source not in {
            "llm_invalid",
            "llm_unavailable",
        }:
            return _record_and_apply(
                llm_decision,
                user_text,
                unresolved_count,
                recent_intents=recent_intents,
                record_metrics=record_metrics,
                after_sales_workflow=after_sales_workflow,
            )

    if settings.intent_rule_fallback:
        ruled = classify_intent_by_rules(
            user_text,
            from_product=from_product,
            consult_card=consult_card,
            message_card=message_card,
            session_intent=session_intent,
        )
        if ruled is not None:
            confidence = 0.9 if ruled != IntentKind.AFTERSALES_UNKNOWN else 0.65
            decision = _build_decision(
                ruled,
                user_text,
                confidence=confidence,
                source="rule",
                after_sales_workflow=after_sales_workflow,
            )
            return _record_and_apply(
                decision,
                user_text,
                unresolved_count,
                recent_intents=recent_intents,
                record_metrics=record_metrics,
                after_sales_workflow=after_sales_workflow,
            )

    decision = _build_decision(
        IntentKind.CHAT,
        user_text,
        confidence=0.4,
        source="default",
        next_action=NextAction.ASK_CLARIFICATION,
        after_sales_workflow=after_sales_workflow,
    )
    return _record_and_apply(
        decision,
        user_text,
        unresolved_count,
        recent_intents=recent_intents,
        record_metrics=record_metrics,
        after_sales_workflow=after_sales_workflow,
    )


def _build_decision(
    intent: IntentKind,
    user_text: str,
    *,
    confidence: float,
    source: str,
    data: str = "",
    next_action: NextAction | None = None,
    after_sales_workflow: bool = False,
) -> IntentDecision:
    sentiment = analyze_sentiment(user_text)
    risk = (
        RiskLevel.HIGH
        if _funds_at_risk(user_text)
        or _is_privacy_request(user_text)
        or _is_account_security_request(user_text)
        or (intent == IntentKind.COMPLAINT and sentiment == SentimentKind.VERY_NEGATIVE)
        else RiskLevel.LOW
    )
    if risk == RiskLevel.LOW and intent in {
        IntentKind.PAYMENT_ISSUE,
        IntentKind.COMPLAINT,
        IntentKind.DAMAGED_OR_WRONG_ITEM,
    }:
        risk = RiskLevel.MEDIUM
    if risk == RiskLevel.LOW and intent in {
        IntentKind.REFUND,
        IntentKind.CANCEL_ORDER,
        IntentKind.CONFIRM_RECEIPT,
        IntentKind.ADDRESS_CHANGE,
        IntentKind.AFTERSALES_UNKNOWN,
    } and classify_request_mode(user_text, intent) == RequestMode.ACTION_PROPOSAL:
        # State-changing proposals need stronger guardrails than read-only
        # policy answers, even before a confirmation token is issued.
        risk = RiskLevel.MEDIUM
    if risk == RiskLevel.LOW and intent == IntentKind.REFUND_STATUS and (
        extract_order_id(user_text)
        or extract_order_item_id(user_text)
        or any(marker in user_text for marker in ("我的退款", "这笔退款", "退款单", "我退的"))
    ):
        risk = RiskLevel.MEDIUM
    urgency = UrgencyKind.NORMAL
    if sentiment == SentimentKind.VERY_NEGATIVE or risk == RiskLevel.HIGH:
        urgency = UrgencyKind.CRITICAL
    elif sentiment == SentimentKind.NEGATIVE or intent in {
        IntentKind.PAYMENT_ISSUE,
        IntentKind.DAMAGED_OR_WRONG_ITEM,
        IntentKind.REFUND_STATUS,
    }:
        urgency = UrgencyKind.HIGH

    if next_action is None:
        tool_intents = _TOOL_INTENTS
        if not after_sales_workflow:
            tool_intents = tool_intents - _AFTER_SALES_WORKFLOW_INTENTS
        next_action = NextAction.TOOL if intent in tool_intents else NextAction.ANSWER
        if (
            intent == IntentKind.REFUND
            and classify_request_mode(user_text, intent) == RequestMode.INFORMATIONAL
            and not (extract_order_id(user_text) or extract_order_item_id(user_text))
        ):
            # Generic refund policy/timing is a knowledge answer. A concrete
            # order remains on the eligibility/tool path.
            next_action = NextAction.ANSWER
        if intent == IntentKind.AFTERSALES_UNKNOWN:
            next_action = NextAction.ASK_CLARIFICATION

    return IntentDecision(
        intent=intent,
        confidence=confidence,
        entities=extract_entities(user_text, data),
        sentiment=sentiment,
        urgency=urgency,
        risk_level=risk,
        next_action=next_action,
        source=source,
        data=data,
    )


def record_intent_metrics(decision: IntentDecision) -> None:
    """记录意图/转人工指标。

    与 _record_and_apply 分离：send 路径与 worker refine 路径各自决定何时
    计数。worker 重算决策（allow_llm=True）时不能再次经过 _record_and_apply
    的计数逻辑，否则同一消息的 INTENT_TOTAL/HANDOFF_TOTAL 被计两次，
    转人工率虚高（P1 审查）。
    """
    INTENT_TOTAL.labels(intent=decision.intent.value, source=decision.source).inc()
    # A5：转人工原因分布必须可查询（误转/漏转都比"少转"难发现）。
    # reason 归一化到固定集合：策略分支写的是集合内常量，但 LLM 决策的
    # handoffReason 是自由文本，直接进 label 会让 Prometheus 基数无限膨胀。
    if decision.next_action in (NextAction.HANDOFF, NextAction.HANDOFF_SUGGESTED) and decision.handoff_reason:
        reason = decision.handoff_reason
        if reason not in _BOUNDED_HANDOFF_REASONS:
            reason = "OTHER"
        HANDOFF_TOTAL.labels(reason=reason).inc()


def _record_and_apply(
    decision: IntentDecision,
    user_text: str,
    unresolved_count: int,
    recent_intents: list[str] | None = None,
    record_metrics: bool = True,
    after_sales_workflow: bool = False,
) -> IntentDecision:
    request_mode = classify_request_mode(user_text, decision.intent)
    if decision.request_mode != request_mode:
        decision = decision.model_copy(update={"request_mode": request_mode})
    entities = {
        **extract_entities(user_text, decision.data),
        **decision.entities,
    }
    if (
        decision.data
        and "orderId" not in entities
        and decision.intent
        in {
            IntentKind.QUERY_ORDER,
            IntentKind.QUERY_LOGISTICS,
            IntentKind.QUERY_FULFILLMENT,
            IntentKind.REFUND_STATUS,
            IntentKind.CANCEL_ORDER,
            IntentKind.CONFIRM_RECEIPT,
        }
    ):
        entities["orderId"] = decision.data
    if entities != decision.entities:
        decision = decision.model_copy(update={"entities": entities})
    decision = _apply_handoff_policy(
        decision, user_text, unresolved_count, recent_intents=recent_intents
    )
    if decision.next_action == NextAction.HANDOFF:
        decision = decision.model_copy(update={"request_mode": RequestMode.HUMAN_SUPPORT})
    if record_metrics:
        record_intent_metrics(decision)
    return decision


def _apply_handoff_policy(
    decision: IntentDecision,
    user_text: str,
    unresolved_count: int,
    recent_intents: list[str] | None = None,
) -> IntentDecision:
    explicit_human = decision.intent == IntentKind.HUMAN_REQUEST or _has_explicit_human_request(
        user_text
    )
    threshold = get_settings().intent_handoff_confidence
    current_unresolved = (
        decision.confidence < threshold
        or decision.next_action == NextAction.ASK_CLARIFICATION
    )
    # A low-confidence classification is not proof that the previous answer failed.
    # Require three consecutive unresolved turns before forcing support; explicit
    # user feedback that the issue is still unresolved continues to hand off now.
    unresolved = (
        unresolved_count >= 2 and current_unresolved
    ) or any(k in user_text for k in _UNRESOLVED_HINTS)
    severe = decision.sentiment == SentimentKind.VERY_NEGATIVE
    fund_dispute = decision.risk_level == RiskLevel.HIGH

    if explicit_human:
        return decision.model_copy(
            update={
                "next_action": NextAction.HANDOFF,
                "handoff_reason": "USER_REQUEST",
                "urgency": UrgencyKind.HIGH,
            }
        )
    if fund_dispute:
        return decision.model_copy(
            update={
                "next_action": NextAction.HANDOFF,
                "handoff_reason": "FUND_DISPUTE",
                "urgency": UrgencyKind.CRITICAL,
            }
        )
    if severe and decision.intent in {
        IntentKind.COMPLAINT,
        IntentKind.PAYMENT_ISSUE,
        IntentKind.DAMAGED_OR_WRONG_ITEM,
        IntentKind.REFUND,
        IntentKind.REFUND_STATUS,
    }:
        return decision.model_copy(
            update={
                "next_action": NextAction.HANDOFF,
                "handoff_reason": "SEVERE_NEGATIVE_SENTIMENT",
            }
        )
    if unresolved:
        return decision.model_copy(
            update={
                "next_action": NextAction.HANDOFF,
                "handoff_reason": "REPEATED_UNRESOLVED",
                "urgency": UrgencyKind.HIGH,
            }
        )
    # A3 死循环检测：同一意图连续 3 轮且这轮仍需要工具/在硬答，说明用户
    # 一直在重复同一诉求而 AI 没帮上忙——主动建议转人工而不是继续车轱辘话
    # （315 翻车案例的根因：循环复读直到用户崩溃）。
    # 误报防护（P1 审查）：当前轮本身是延续/应答（"然后呢""好的呢"）时
    # 不算"重复诉求"——否则 1 句真诉求 + 2 句应答就能凑满 3 连触发建议。
    from app.domain.intent.rules import (
        looks_like_ack_or_greeting,
        looks_like_intent_continuation,
    )

    # A greeting or an explicit payment-method question is a known harmless
    # CHAT turn. The default classifier confidence is intentionally low, but a
    # first such turn must not immediately suggest a human handoff. Repeated
    # unresolved turns still follow the existing escalation contract.
    safe_chat = decision.intent == IntentKind.CHAT and (
        looks_like_ack_or_greeting(user_text)
        or any(marker in user_text for marker in _PAYMENT_INFORMATION_HINTS)
    )
    if (
        safe_chat
        and unresolved_count == 0
        and decision.next_action
        in {NextAction.ASK_CLARIFICATION, NextAction.HANDOFF_SUGGESTED}
    ):
        return decision.model_copy(
            update={"next_action": NextAction.ANSWER, "handoff_reason": None}
        )

    # recent_intents 只包含已经落库的历史轮次，不包含当前 decision。因此
    # “当前轮 + 最近两轮”才是连续 3 轮；要求三条历史会拖到第 4 轮才触发。
    repeated_intent = (
        recent_intents
        and len(recent_intents) >= 2
        and all(i == decision.intent.value for i in recent_intents[:2])
        and decision.next_action == NextAction.TOOL
        and not looks_like_intent_continuation(user_text, decision.intent.value)
        and not looks_like_ack_or_greeting(user_text)
    )
    if repeated_intent:
        return decision.model_copy(
            update={
                "next_action": NextAction.HANDOFF_SUGGESTED,
                "handoff_reason": "REPEATED_INTENT",
                "urgency": UrgencyKind.HIGH,
            }
        )
    if decision.confidence < threshold:
        return decision.model_copy(
            update={
                "next_action": NextAction.HANDOFF_SUGGESTED,
                "handoff_reason": "LOW_CONFIDENCE",
            }
        )
    return decision


def _clamp_confidence(value, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _enum_or_default(enum_type, value, default):
    try:
        return enum_type(str(value).upper())
    except (TypeError, ValueError):
        return default
