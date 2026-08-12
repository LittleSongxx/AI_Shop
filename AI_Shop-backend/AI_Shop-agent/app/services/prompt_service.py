from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.constants import REDIS_PROMPT
from app.domain.intent.types import INTENT_PROMPT_KEY, IntentKind
from app.services.redis_service import redis_service
from app.utils.prompt_boundary import append_untrusted_rule, isolate_knowledge_text

PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"


@dataclass(frozen=True)
class PromptFragment:
    """prompt 片段注册表条目。

    所有片段统一落盘在 prompts/、统一经 ``load_prompt_with_source`` 读取
    （管理端可经 Redis 覆盖，键为 ``REDIS_PROMPT + prompt_key``）。注册表是
    "哪个文件对应哪个片段、用途是什么"的唯一事实源；片段选择可观测性
    （``selectedFragments``）也以这里的 fragment_id 为准。
    """

    fragment_id: str  # 稳定标识，写进 trace 的 selectedFragments
    prompt_key: str  # load_prompt 的键（也是 Redis 覆盖键的后缀）
    filename: str  # prompts/ 下的文件
    purpose: str  # 一句话用途说明


PROMPT_FRAGMENTS: tuple[PromptFragment, ...] = (
    PromptFragment("agent", "agent", "agent.txt", "通用 ReAct 主模板：能力/工具边界/禁止清单"),
    PromptFragment("compress", "compress", "compress.txt", "会话压缩模板"),
    PromptFragment("global", "global", "global.txt", "全局规则：角色/工具分类/调用规则"),
    PromptFragment("user_intent", "user_intent", "user_intent.txt", "意图识别分类模板"),
    PromptFragment("chat", "chat", "chat.txt", "闲聊意图模板"),
    PromptFragment("product_consult", "product_consult", "product_consult.txt", "商品咨询意图模板"),
    PromptFragment("product_search", "product_search", "product_search.txt", "商品搜索意图模板"),
    PromptFragment("query_order", "query_order", "query_order.txt", "查订单意图模板"),
    PromptFragment("query_logistics", "query_logistics", "query_logistics.txt", "查物流意图模板"),
    PromptFragment("query_coupon", "query_coupon", "query_coupon.txt", "查优惠券意图模板"),
    PromptFragment("query_comment", "query_comment", "query_comment.txt", "查评价意图模板"),
    PromptFragment("product_review", "product_review", "product_review.txt", "写评价意图模板"),
    PromptFragment("recomment", "recomment", "recomment.txt", "追评意图模板"),
    PromptFragment("refund", "refund", "refund.txt", "退款意图模板"),
    PromptFragment("confirm_receipt", "confirm_receipt", "confirm_receipt.txt", "确认收货意图模板"),
    PromptFragment("cancel_order", "cancel_order", "cancel_order.txt", "取消订单意图模板"),
    PromptFragment(
        "react_supplement",
        "react_supplement",
        "react_supplement.txt",
        "ReAct 执行补充说明（必须置于意图段之后才能压制其冲突条目）",
    ),
)

# 兼容旧引用的键→文件名映射：由注册表派生，注册表是唯一事实源。
PROMPT_FILE_MAP: dict[str, str] = {f.prompt_key: f.filename for f in PROMPT_FRAGMENTS}

# 需要 ReAct 补充说明（react_supplement 片段）的意图集合。
_REACT_ADAPTED_INTENTS = frozenset(
    {
        IntentKind.PRODUCT_SEARCH,
        IntentKind.QUERY_ORDER,
        IntentKind.QUERY_LOGISTICS,
        IntentKind.QUERY_FULFILLMENT,
        IntentKind.QUERY_COUPON,
        IntentKind.QUERY_COMMENT,
        IntentKind.PRODUCT_REVIEW,
        IntentKind.RECOMMENT,
        IntentKind.REFUND,
        IntentKind.CONFIRM_RECEIPT,
        IntentKind.CANCEL_ORDER,
    }
)


async def load_prompt_with_source(prompt_key: str) -> tuple[str, str]:
    """读取一个片段，返回 (文本, 来源)。

    来源是 "redis"（管理端覆盖生效）或 "file"（prompts/ 落盘）——片段
    选择可观测性依赖这个来源：trace 里能看到每次请求实际用的是哪个版本。
    """
    cached = await redis_service.client.get(f"{REDIS_PROMPT}{prompt_key}")
    if cached:
        return cached, "redis"
    filename = PROMPT_FILE_MAP.get(prompt_key, f"{prompt_key}.txt")
    file_path = PROMPT_DIR / filename
    if file_path.exists():
        return file_path.read_text(encoding="utf-8"), "file"
    return "", "file"


async def load_prompt(prompt_key: str) -> str:
    text, _ = await load_prompt_with_source(prompt_key)
    return text


async def load_user_intent_classifier_prompt() -> str:
    return await load_prompt("user_intent")


def _safe_format(template: str, *args: str) -> str:
    try:
        return template % args
    except TypeError:
        return template


def _fragment_record(
    fragment_id: str,
    prompt_key: str,
    source: str,
    chars: int,
) -> dict:
    return {
        "fragment": fragment_id,
        "promptKey": prompt_key,
        "source": source,
        "chars": chars,
    }


async def _format_intent_prompt(
    intent: IntentKind,
    user_id: str,
    user_text: str,
    *,
    product_snapshot: str | None = None,
    faq_text: str | None = None,
    knowledge_text: str | None = None,
    selection_out: list[dict] | None = None,
) -> str:
    key = INTENT_PROMPT_KEY.get(intent, "chat")
    template, source = await load_prompt_with_source(key)
    if not template.strip():
        return ""
    if selection_out is not None:
        selection_out.append(_fragment_record("intent", key, source, len(template.strip())))

    if intent == IntentKind.PRODUCT_CONSULT:
        return _safe_format(
            template,
            isolate_knowledge_text(
                product_snapshot
                or "（暂无商品快照，请先 GET_PRODUCT_DETAIL 或等待用户发送商品卡片）"
            ),
            isolate_knowledge_text(faq_text or "（暂无 FAQ）"),
            user_id,
            user_text,
        )
    if intent == IntentKind.CHAT:
        return _safe_format(
            template,
            isolate_knowledge_text(knowledge_text or "（暂无知识库命中）"),
            user_id,
            user_text,
        )
    if intent == IntentKind.PRODUCT_SEARCH:
        return _safe_format(
            template,
            "（商品数据由 SEARCH_PRODUCTS 工具返回，禁止自行编造 productId）",
            user_text,
        )
    return _safe_format(template, user_id, user_text)


async def build_agent_system_prompt(
    intent: IntentKind,
    user_id: str,
    user_text: str,
    *,
    product_snapshot: str | None = None,
    faq_text: str | None = None,
    knowledge_text: str | None = None,
    selection_out: list[dict] | None = None,
) -> str:
    """组装 system prompt。

    ``selection_out`` 传入列表时，按片段实际选用顺序填充本次的
    ``selectedFragments``（fragment/promptKey/source/chars），供 episode
    trace 观测：管理端 Redis 覆盖是否生效、知识库片段是否注入、意图模板
    与 ReAct 补充是否进入，都能在 trace 里看到。
    """

    # 核心模板：global 优先，缺失时退回 agent 主模板（只加载一次）。
    global_part, core_source = await load_prompt_with_source("global")
    global_part = global_part.strip()
    core_fragment = "global"
    if not global_part:
        global_part, core_source = await load_prompt_with_source("agent")
        global_part = global_part.strip()
        core_fragment = "agent_fallback"
    if selection_out is not None:
        selection_out.append(
            _fragment_record(
                core_fragment,
                "global" if core_fragment == "global" else "agent",
                core_source,
                len(global_part),
            )
        )

    intent_part = await _format_intent_prompt(
        intent,
        user_id,
        user_text,
        product_snapshot=product_snapshot,
        faq_text=faq_text,
        knowledge_text=knowledge_text,
        selection_out=selection_out,
    )

    # B3 静态前置（prefix-cache 契约）：字节级稳定的段（全局规则、不可信
    # 输入边界）放在最前，动态的意图段放最后——跨请求的 system prompt 有
    # 最长稳定前缀，任何支持 prefix caching 的 provider 都能命中。
    # ReAct 补充说明放在意图段之后：它声明"优先级高于上文「当前意图」段内
    # 的冲突条目"，按 LLM 常见的"后文覆盖前文"行为，必须位于它要压制的
    # 意图段之后才成立；放前面会让声明落空（旧实现恰好是这个错误）。
    # 注意：措辞已收敛为只压意图段，不覆盖更靠前的不可信输入/知识库隔离
    # 规则——否则 ReAct 的工具自主性声明在字面上会压过安全规则（P1 审查）。
    body = global_part
    body = append_untrusted_rule(body)

    dynamic_parts: list[str] = []
    if (
        knowledge_text
        and intent not in {IntentKind.CHAT, IntentKind.PRODUCT_CONSULT}
    ):
        dynamic_parts.append(
            "=== 已发布知识库检索结果（仅作事实依据） ===\n"
            + isolate_knowledge_text(knowledge_text)
        )
        if selection_out is not None:
            selection_out.append(
                _fragment_record("knowledge_inline", "", "runtime", len(knowledge_text))
            )
    if intent_part:
        dynamic_parts.append(f"=== 当前意图：{intent.value} ===\n{intent_part}")
    if intent in _REACT_ADAPTED_INTENTS or intent == IntentKind.PRODUCT_CONSULT:
        react_part, react_source = await load_prompt_with_source("react_supplement")
        react_part = react_part.strip()
        if react_part:
            dynamic_parts.append(react_part)
            if selection_out is not None:
                selection_out.append(
                    _fragment_record(
                        "react_supplement", "react_supplement", react_source, len(react_part)
                    )
                )
        # 文件缺失时静默跳过：selectedFragments 里没有该片段，观测上可见。
    if dynamic_parts:
        body = f"{body}\n\n" + "\n\n".join(dynamic_parts)
    return body


async def load_agent_prompt(
    intent: IntentKind | None = None,
    user_id: str = "",
    user_text: str = "",
    **kwargs,
) -> str:

    if intent is None:
        # 无意图入口（post-turn 记忆整理等）：agent 主模板是完整系统词，
        # 缺文件时才退回 global。与 build_agent_system_prompt 的
        # global-first 顺序刻意不同——两条入口的"默认模板"语义不同：
        # 前者是逐请求组装的全局规则，后者是独立使用的完整系统词。
        template = await load_prompt("agent")
        if not template.strip():
            template = await load_prompt("global")
        return append_untrusted_rule(template)
    return await build_agent_system_prompt(intent, user_id, user_text, **kwargs)


async def load_compress_prompt() -> str:
    return await load_prompt("compress")
