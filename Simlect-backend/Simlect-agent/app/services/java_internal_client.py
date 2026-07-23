from typing import Any

import httpx
import structlog

from app.config.settings import get_settings

logger = structlog.get_logger()


def _camel_to_snake(name: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and (not name[i - 1].isupper() or (i + 1 < len(name) and name[i + 1].islower())):
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def normalize_keys(obj: Any) -> Any:

    if isinstance(obj, list):
        return [normalize_keys(x) for x in obj]
    if isinstance(obj, dict):
        return {_camel_to_snake(str(k)): normalize_keys(v) for k, v in obj.items()}
    return obj


class JavaInternalClient:
    """Calls Java /internal/** exclusively via Gateway (java_web_url)."""

    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        settings = get_settings()
        return {
            "X-Internal-Token": settings.internal_token,
            "Content-Type": "application/json",
        }

    def _base(self) -> str:
        return get_settings().java_web_url.rstrip("/")

    async def post_json(self, path: str, body: dict | None = None) -> Any:
        url = f"{self._base()}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=body or {}, headers=self._headers())
                resp.raise_for_status()
                payload = resp.json()
        except Exception as e:
            logger.error("java_internal_http_failed", url=url, error=str(e))
            raise

        if not isinstance(payload, dict):
            raise ValueError(f"invalid ResponseVO from {url}")
        status = payload.get("status")
        code = payload.get("code", 200)
        if status == "error" or (status is not None and status != "success"):
            raise ValueError(payload.get("info") or f"internal call failed: {url}")
        if status is None and code not in (200, "200", None):
            raise ValueError(payload.get("info") or f"internal call failed: {url}")
        return payload.get("data")

    async def list_orders(
        self,
        user_id: str,
        order_id: str | None = None,
        time_start: str | None = None,
        time_end: str | None = None,
        limit: int | None = 30,
    ) -> list[dict]:
        body: dict[str, Any] = {"userId": user_id}
        if order_id:
            body["orderId"] = order_id
        if time_start:
            body["timeStart"] = time_start
        if time_end:
            body["timeEnd"] = time_end
        if limit is not None:
            body["limit"] = limit
        data = await self.post_json("/internal/order/agent/listOrders", body)
        return normalize_keys(data or [])

    async def get_order(self, order_id: str) -> dict | None:
        data = await self.post_json(
            "/internal/order/agent/getOrder",
            {"orderId": order_id},
        )
        return normalize_keys(data) if data else None

    async def get_order_item(self, order_item_id: str) -> dict | None:
        data = await self.post_json(
            "/internal/order/agent/getOrderItem",
            {"orderItemId": order_item_id},
        )
        return normalize_keys(data) if data else None

    async def list_order_items(self, order_id: str) -> list[dict]:
        data = await self.post_json(
            "/internal/order/agent/listOrderItems",
            {"orderId": order_id},
        )
        return normalize_keys(data or [])

    async def get_logistics(self, user_id: str, order_id: str) -> dict | None:
        data = await self.post_json(
            "/internal/order/agent/getLogistics",
            {"userId": user_id, "orderId": order_id},
        )
        return normalize_keys(data) if data else None

    async def get_comment(self, user_id: str, order_id: str) -> dict | None:
        data = await self.post_json(
            "/internal/order/agent/getComment",
            {"userId": user_id, "orderId": order_id},
        )
        return normalize_keys(data) if data else None

    async def snapshot_batch(self, product_ids: list[str]) -> dict | None:
        data = await self.post_json(
            "/internal/product/snapshotBatch",
            {"productIds": product_ids},
        )
        return normalize_keys(data) if data else None

    async def search_on_sale(
        self,
        keyword: str | None = None,
        limit: int = 20,
        category_id: str | None = None,
        hot_sale: bool = False,
    ) -> list[dict]:
        body: dict[str, Any] = {"keyword": keyword or "", "limit": limit}
        if category_id:
            body["categoryId"] = category_id
        if hot_sale:
            body["hotSale"] = True
        data = await self.post_json(
            "/internal/product/agent/searchOnSale",
            body,
        )
        return normalize_keys(data or [])

    async def get_product_detail(self, product_id: str) -> dict | None:
        data = await self.post_json(
            "/internal/product/agent/getDetail",
            {"productId": product_id},
        )
        return normalize_keys(data) if data else None

    async def list_user_coupons(self, user_id: str) -> list[dict]:
        data = await self.post_json(
            "/internal/coupon/agent/listUserCoupons",
            {"userId": user_id},
        )
        return normalize_keys(data or [])

    async def latest_browse_product_id(self, user_id: str) -> str | None:
        data = await self.post_json(
            "/internal/user/agent/latestBrowseProductId",
            {"userId": user_id},
        )
        if not data:
            return None
        if isinstance(data, dict):
            pid = data.get("productId") or data.get("product_id")
            return str(pid) if pid else None
        return str(data)

    async def knowledge_version(self) -> int:
        data = await self.post_json("/internal/search/knowledge/version", {})
        try:
            return int(data)
        except (TypeError, ValueError):
            return 1

    async def exact_faq(
        self,
        question: str,
        language: str = "zh-CN",
        channel: str = "web",
    ) -> dict | None:
        data = await self.post_json(
            "/internal/search/knowledge/faqExact",
            {"question": question, "language": language, "channel": channel},
        )
        return normalize_keys(data) if data else None

    async def top_faq(self, limit: int = 100) -> list[dict]:
        data = await self.post_json(
            "/internal/search/knowledge/topFaq",
            {"limit": limit},
        )
        return normalize_keys(data or [])

    async def submit_faq_candidate(
        self,
        question: str,
        answer: str,
        source_message_id: int | None = None,
        category: str = "general",
    ) -> None:
        body: dict[str, Any] = {
            "question": question,
            "answer": answer,
            "category": category,
        }
        if source_message_id is not None:
            body["sourceMessageId"] = source_message_id
        await self.post_json("/internal/search/knowledge/faqCandidate", body)


java_internal_client = JavaInternalClient()
