from __future__ import annotations

import json
import re

import structlog

from app.memory.assistant_condense import schedule_assistant_condense, truncate_assistant_for_history
from app.memory.compress_service import compress_service
from app.memory.models import SessionMemory
from app.memory.session_memory_service import session_memory_service
from app.services.prompt_service import load_agent_prompt
from app.services.redis_service import redis_service

logger = structlog.get_logger()

_BIZ_MARKER = re.compile(r"<!--BIZ:([\s\S]*?)-->", re.I)

class PostTurnService:

    async def run(
        self,
        user_id: str,
        message_id: int,
        user_text: str,
        assistant_text: str,
        tools_called: list[str],
        tool_biz: dict | None,
        card: dict | None,
        working_turns: list[dict],
        working_oldest_id: int | None,
    ) -> None:
        memory = await session_memory_service.load(user_id, redis_service.client)

        memory.state["turnCount"] = int(memory.state.get("turnCount") or 0) + 1

        await self._sync_consult_product(user_id, memory, card)
        await self._sync_pending_action(user_id, memory, assistant_text)
        self._sync_tool_results(memory, tools_called, tool_biz, assistant_text)

        await session_memory_service.save(memory, redis_service.client)

        system_prompt = await load_agent_prompt()

        await compress_service.maybe_schedule_compress(
            user_id,
            memory,
            working_turns,
            working_oldest_id,
            user_text,
            system_prompt,
        )

        immediate = truncate_assistant_for_history(assistant_text)
        if immediate:
            await redis_service.save_history_condensed(user_id, message_id, immediate)

        schedule_assistant_condense(user_id, message_id, assistant_text)

    async def _sync_consult_product(
        self, user_id: str, memory: SessionMemory, card: dict | None
    ) -> None:

        if card and card.get("productId"):
            memory.state["consultProduct"] = {
                "productId": str(card["productId"]),
                "productName": card.get("productName"),
                "minPrice": card.get("minPrice"),
                "cover": card.get("cover"),
            }
            return

        cached = await redis_service.get_consult_product(user_id)
        if not cached:
            return
        normalized = cached

        memory.state["consultProduct"] = {
            "productId": str(normalized.get("productId") or normalized.get("product_id") or ""),
            "productName": normalized.get("productName") or normalized.get("product_name"),
            "minPrice": normalized.get("minPrice") or normalized.get("min_price"),
            "categoryId": normalized.get("categoryId") or normalized.get("category_id"),
            "cover": normalized.get("cover"),
        }

    async def _sync_pending_action(self, user_id: str, memory: SessionMemory, assistant_text: str) -> None:

        from app.services.pending_action_service import pending_action_service
        from app.utils.biz_payload import extract_act_token_id

        token_id = extract_act_token_id(assistant_text or "")
        if not token_id:
            memory.state["pendingAction"] = None
            return
        pending = await pending_action_service.get_by_token(token_id)

        if not pending or pending.get("userId") != user_id:
            memory.state["pendingAction"] = None
            return
        memory.state["pendingAction"] = {
            "token": pending.get("token"),
            "actionType": pending.get("actionType"),
            "summary": pending.get("summary"),
        }

    def _sync_tool_results(
        self,
        memory: SessionMemory,
        tools_called: list[str],
        tool_biz: dict | None,
        assistant_text: str,
    ) -> None:

        last = memory.state.setdefault(
            "lastToolResults",
            {
                "searchedProducts": [],
                "searchedProductNames": [],
                "queriedOrders": [],
                "viewedProductIds": [],
            },
        )

        if tool_biz:
            if tool_biz.get("productIds"):
                last["searchedProducts"] = tool_biz["productIds"][:12]
            if tool_biz.get("productNames"):
                last["searchedProductNames"] = tool_biz["productNames"][:12]
            if tool_biz.get("orderIds"):
                last["queriedOrders"] = tool_biz["orderIds"][:12]
            return

        marker = _BIZ_MARKER.search(assistant_text or "")
        if marker:
            try:
                payload = json.loads(marker.group(1))
                if payload.get("productIds"):
                    last["searchedProducts"] = payload["productIds"]
                if payload.get("productNames"):
                    last["searchedProductNames"] = payload["productNames"]
            except json.JSONDecodeError:
                pass

        if "SEARCH_PRODUCTS" in tools_called and not last.get("searchedProductNames"):
            last["searchedProductNames"] = []
        if "QUERY_ORDERS" in tools_called and not last.get("queriedOrders"):
            last["queriedOrders"] = []

post_turn_service = PostTurnService()
