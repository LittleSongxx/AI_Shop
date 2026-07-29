from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.config.settings import get_settings
from app.domain.intent.types import IntentKind
from app.memory.models import SessionMemory
from app.memory.token_estimator import estimate_text_tokens
from app.services.message_service import agent_message_service
from app.services.prompt_service import build_agent_system_prompt
from app.services.shopping_profile_service import _has_signal, shopping_profile_service
from app.utils.prompt_boundary import isolate_user_message


def _assistant_for_context(turn: dict) -> str:

    condensed = (turn.get("assistant_for_history") or "").strip()
    if condensed:
        return condensed
    assistant = (turn.get("assistant_message") or "").strip()

    if assistant and agent_message_service.should_include_in_working_memory(assistant):
        return assistant
    return ""

def is_complete_turn_for_context(turn: dict) -> bool:

    user = (turn.get("user_message") or "").strip()

    return bool(user and _assistant_for_context(turn))

def estimate_turn_tokens(turn: dict) -> int:

    if not is_complete_turn_for_context(turn):
        return 0
    user_text = turn.get("user_message") or ""
    assistant_text = _assistant_for_context(turn)

    return estimate_text_tokens(user_text) + estimate_text_tokens(assistant_text) + 8

def select_working_turns(
    turns: list[dict],
    after_message_id: int,
    token_budget: int,
) -> tuple[list[dict], int | None]:

    eligible = [
        t for t in turns
        if int(t["message_id"]) > after_message_id and is_complete_turn_for_context(t)
    ]
    if not eligible:

        return [], None

    selected_rev: list[dict] = []
    used = 0

    for turn in reversed(eligible):
        turn_tokens = estimate_turn_tokens(turn)
        if turn_tokens <= 0:
            continue

        if turn_tokens > token_budget:
            continue

        if selected_rev and used + turn_tokens > token_budget:
            break
        selected_rev.append(turn)
        used += turn_tokens

    selected = list(reversed(selected_rev))
    oldest_id = int(selected[0]["message_id"]) if selected else None
    return selected, oldest_id

def build_context_block(memory: SessionMemory, shopping_profile: dict | None = None) -> str:

    summary = memory.summary
    facts = summary.get("facts") or {}
    state = memory.state
    lines = ["## 会话摘要"]
    narrative = (summary.get("narrative") or "").strip()
    if narrative:
        lines.append(narrative)
    goal = facts.get("goal")
    budget = facts.get("budget")

    preferences = facts.get("preferences") or []
    decisions = facts.get("decisions") or []
    meta_parts = []
    if goal:
        meta_parts.append(f"目标: {goal}")
    # Structured profile is the authoritative source for budget/brand constraints.
    # Only fall back to LLM-compressed summary facts when the profile has no signal —
    # the LLM can hallucinate or forget exact numbers during summarisation.
    profile_has_signal = _has_signal(shopping_profile or {})
    if not profile_has_signal:
        if budget:
            meta_parts.append(f"预算: {budget}")
        if preferences:
            meta_parts.append(f"偏好: {', '.join(map(str, preferences))}")
    if decisions:
        meta_parts.append(f"已决策: {', '.join(map(str, decisions[-8:]))}")
    if meta_parts:
        lines.append(" | ".join(meta_parts))

    # Shopping profile — structured, durable, regex-extracted constraints.
    # Shown only when the profile has accumulated signal; absent on first contact
    # or pure customer-service sessions that never touch product browsing/buying.
    if profile_has_signal:
        profile_summary = shopping_profile_service.summary(shopping_profile)
        if profile_summary:
            lines.append("\n## 购物偏好")
            lines.append(profile_summary)

    lines.append("\n## 当前状态")
    consult = state.get("consultProduct")
    if consult and consult.get("productName"):
        price = consult.get("minPrice")

        price_txt = f" (¥{price})" if price is not None else ""
        lines.append(f"咨询商品: {consult.get('productName')}{price_txt}")
    else:
        lines.append("咨询商品: 无")

    pending = state.get("pendingAction")
    if pending and pending.get("summary"):
        lines.append(f"待确认操作: {pending.get('summary')}")
    else:
        lines.append("待确认操作: 无")

    last = state.get("lastToolResults") or {}
    if last.get("searchedProductNames") or last.get("searchedProducts"):
        lines.append("上轮曾搜索过商品（需重新调用 SEARCH_PRODUCTS 获取最新结果）")
    else:
        lines.append("上轮搜索结果: 无")

    return "\n".join(lines)

class ContextBuilder:

    async def build_agent_messages(
        self,
        user_id: str,
        user_text: str,
        memory: SessionMemory,
        *,
        intent: IntentKind = IntentKind.CHAT,
        product_snapshot: str | None = None,
        faq_text: str | None = None,
        knowledge_text: str | None = None,
    ) -> tuple[list, list[dict], int | None]:

        settings = get_settings()
        # Build system prompt and load shopping profile concurrently —
        # the profile is a fast Redis hit in the common case, so this is free.
        system_prompt, shopping_profile = await asyncio.gather(
            build_agent_system_prompt(
                intent,
                user_id,
                user_text,
                product_snapshot=product_snapshot,
                faq_text=faq_text,
                knowledge_text=knowledge_text,
            ),
            shopping_profile_service.get_profile(user_id),
        )
        context_block = build_context_block(memory, shopping_profile)

        turns = await agent_message_service.load_turns_for_memory(user_id)
        working_turns, working_oldest_id = select_working_turns(
            turns,
            memory.summary_last_message_id,
            settings.working_token_budget,
        )

        messages: list = [SystemMessage(content=system_prompt)]
        messages.append(SystemMessage(content=context_block))

        for turn in working_turns:
            if not is_complete_turn_for_context(turn):
                continue
            user_msg = turn.get("user_message")
            assistant_msg = _assistant_for_context(turn)

            messages.append(HumanMessage(content=isolate_user_message(user_msg)))
            messages.append(AIMessage(content=assistant_msg))

        messages.append(HumanMessage(content=isolate_user_message(user_text)))
        return messages, working_turns, working_oldest_id

    def estimate_context_tokens(
        self,
        memory: SessionMemory,
        working_turns: list[dict],
        user_text: str,
        system_prompt: str,
        shopping_profile: dict | None = None,
    ) -> int:

        total = estimate_text_tokens(system_prompt)
        total += estimate_text_tokens(build_context_block(memory, shopping_profile))
        for turn in working_turns:
            if not is_complete_turn_for_context(turn):
                continue
            total += estimate_text_tokens(turn.get("user_message"))
            total += estimate_text_tokens(_assistant_for_context(turn))
        total += estimate_text_tokens(user_text)
        return total

context_builder = ContextBuilder()
