import contextvars
from typing import Any

import structlog

from app.config.settings import get_settings
from app.infra.http_client import get_client

logger = structlog.get_logger()

# 委托用户身份：由 Agent worker 从会话身份（系统信道）写入，作为 X-Agent-User-Id
# 传给 Java 内部接口。与 body 里模型可见的 userId 分离——模型输出或提示注入可以
# 改写 body，改不了头；Java 侧比对二者一致性，不一致即拒绝。
_delegated_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "java_internal_delegated_user_id", default=""
)


def set_delegated_user_id(user_id: str | None) -> None:
    """在当前异步任务上下文中声明本次会话代表哪个用户。"""
    _delegated_user_id.set((user_id or "").strip())


def clear_delegated_user_id() -> None:
    _delegated_user_id.set("")


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
        headers = {
            "X-Internal-Token": settings.internal_token,
            "Content-Type": "application/json",
        }
        delegated = _delegated_user_id.get()
        if delegated:
            headers["X-Agent-User-Id"] = delegated
        return headers

    def _base(self) -> str:
        return get_settings().java_web_url.rstrip("/")

    async def post_json(self, path: str, body: dict | None = None) -> Any:
        url = f"{self._base()}/{path.lstrip('/')}"
        try:
            client = await get_client("java_internal", timeout=self._timeout)
            resp = await client.post(
                url, json=body or {}, headers=self._headers(), timeout=self._timeout
            )
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

    async def post_bytes(
        self, path: str, body: dict | None = None, *, timeout: float | None = None
    ) -> tuple[bytes, dict[str, str]]:
        url = f"{self._base()}/{path.lstrip('/')}"
        request_timeout = timeout or self._timeout
        try:
            client = await get_client("java_internal", timeout=request_timeout)
            response = await client.post(
                url,
                json=body or {},
                headers=self._headers(),
                timeout=request_timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.error(
                "java_internal_binary_http_failed",
                path=path,
                error=type(exc).__name__,
            )
            raise
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type.lower():
            try:
                payload = response.json()
            except ValueError as exc:
                raise ValueError("invalid binary response from Java") from exc
            raise ValueError(
                str(payload.get("info") or "Java image asset read was rejected")
            )
        if not response.content:
            raise ValueError("Java image asset response was empty")
        return response.content, dict(response.headers)

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

    async def get_refund_status(
        self,
        user_id: str,
        *,
        order_id: str | None = None,
        order_item_id: str | None = None,
    ) -> list[dict]:
        body: dict[str, Any] = {"userId": user_id}
        if order_id:
            body["orderId"] = order_id
        if order_item_id:
            body["orderItemId"] = order_item_id
        data = await self.post_json("/internal/order/agent/refundStatus", body)
        return normalize_keys(data or [])

    async def get_agent_action_status(
        self,
        user_id: str,
        action_type: str,
        idempotency_key: str,
        params: dict,
        *,
        max_attempts: int | None = None,
        reconcile_window_seconds: int | None = None,
    ) -> dict:
        body = {
            "userId": user_id,
            "actionType": action_type,
            "idempotencyKey": idempotency_key,
            "params": params,
        }
        if max_attempts is not None:
            body["maxAttempts"] = int(max_attempts)
        if reconcile_window_seconds is not None:
            body["reconcileWindowSeconds"] = int(reconcile_window_seconds)
        data = await self.post_json(
            "/internal/order/agent/actionStatus",
            body,
        )
        return normalize_keys(data) if isinstance(data, dict) else {}

    async def snapshot_batch(self, product_ids: list[str]) -> dict | None:
        data = await self.post_json(
            "/internal/product/snapshotBatch",
            {"productIds": product_ids},
        )
        return normalize_keys(data) if data else None

    async def offer_snapshot_batch(
        self, user_id: str, product_ids: list[str]
    ) -> dict | None:
        """Return Java-owned, SKU-level sellability facts for Agent ranking."""
        data = await self.post_json(
            "/internal/product/agent/offerSnapshots",
            {"userId": user_id, "productIds": product_ids},
        )
        return normalize_keys(data) if isinstance(data, dict) else None

    async def estimate_single_sku_offers(
        self, user_id: str, items: list[dict[str, Any]]
    ) -> list[dict]:
        """Ask Coupon service for a single-SKU estimate; never calculate locally."""
        data = await self.post_json(
            "/internal/coupon/agent/estimateSingleSkuOffers",
            {"userId": user_id, "items": items},
        )
        return normalize_keys(data or []) if isinstance(data, list) else []

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

    async def list_on_sale_product_ids(self) -> list[str]:
        data = await self.post_json("/internal/product/listOnSaleProductIds", {})
        if not isinstance(data, list):
            return []
        return [str(product_id) for product_id in data if str(product_id or "").strip()]

    async def get_product_rag_index(self, product_id: str) -> dict | None:
        data = await self.post_json(
            "/internal/product/ragIndex", {"productId": product_id}
        )
        return normalize_keys(data) if isinstance(data, dict) else None

    async def fetch_product_image(
        self, product_id: str, cover_index: int, *, timeout: float | None = None
    ) -> tuple[bytes, dict[str, str]]:
        return await self.post_bytes(
            "/internal/product/agent/imageContent",
            {"productId": product_id, "coverIndex": int(cover_index)},
            timeout=timeout,
        )

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

    async def browse_history_ids(self, user_id: str, limit: int = 5) -> list[str]:
        """Return the most-recently browsed product IDs for the user.

        POST /internal/user/agent/browseHistoryIds
        Body: {"userId": "...", "limit": N}
        Data: list of productId strings (newest first, deduplicated by Java side)
        """
        try:
            data = await self.post_json(
                "/internal/user/agent/browseHistoryIds",
                {"userId": user_id, "limit": max(1, limit)},
            )
            if not data or not isinstance(data, list):
                return []
            ids: list[str] = []
            for item in data:
                if isinstance(item, dict):
                    pid = item.get("productId") or item.get("product_id")
                    if pid:
                        ids.append(str(pid))
                elif item:
                    ids.append(str(item))
            return ids
        except Exception:
            return []

    async def verify_agent_image(self, user_id: str, image_asset_id: str) -> dict:
        data = await self.post_json(
            "/internal/user/agent/verifyImage",
            {
                "userId": user_id,
                "imageAssetId": image_asset_id,
            },
        )
        return normalize_keys(data) if isinstance(data, dict) else {}

    async def fetch_agent_image(
        self, user_id: str, image_asset_id: str, *, timeout: float | None = None
    ) -> tuple[bytes, dict[str, str]]:
        return await self.post_bytes(
            "/internal/user/agent/imageContent",
            {"userId": user_id, "imageAssetId": image_asset_id},
            timeout=timeout,
        )

    async def retain_agent_image_as_support_evidence(
        self, user_id: str, image_asset_id: str
    ) -> None:
        await self.post_json(
            "/internal/user/agent/retainImageAsSupportEvidence",
            {"userId": user_id, "imageAssetId": image_asset_id},
        )

    async def send_user_notification(
        self,
        user_id: str,
        *,
        title: str,
        content: str,
        biz_type: str,
        biz_id: str,
    ) -> None:
        """Use the existing Java notification outbox and WebSocket push path."""
        await self.post_json(
            "/internal/user/notify/sendAsync",
            {
                "userId": user_id,
                "title": title,
                "content": content,
                "bizType": biz_type,
                "bizId": biz_id,
            },
        )

    async def purchase_history_product_ids(self, user_id: str, limit: int = 3) -> list[str]:
        """Return product IDs from the user's recent completed orders.

        Filters to "product received" statuses — COMPLETED (3), WAIT_COMMENT (8),
        PARTIALLY_REFUNDED (7) — so cancelled / unpaid orders are excluded.
        Reuses the existing listOrders endpoint; no new Java endpoint required.
        """
        from app.constants import (
            ORDER_STATUS_COMPLETED,
            ORDER_STATUS_PARTIALLY_REFUNDED,
            ORDER_STATUS_WAIT_COMMENT,
        )

        received = {ORDER_STATUS_COMPLETED, ORDER_STATUS_WAIT_COMMENT, ORDER_STATUS_PARTIALLY_REFUNDED}
        try:
            # Over-fetch orders to get enough completed ones.
            orders = await self.list_orders(user_id, limit=max(1, limit) * 3)
            ids: list[str] = []
            for order in orders:
                if order.get("order_status") not in received:
                    continue
                for item in order.get("items") or []:
                    pid = str(item.get("product_id") or "").strip()
                    if pid and pid not in ids:
                        ids.append(pid)
                        if len(ids) >= limit:
                            return ids
            return ids
        except Exception:
            return []

    async def co_purchase_product_ids(self, product_id: str, limit: int = 5) -> list[str]:
        """Return product IDs most frequently co-purchased with the given product.

        POST /internal/order/agent/coPurchaseProductIds
        Body: {"productId": "...", "limit": N}
        Data: list of productId strings sorted by co-purchase frequency desc.
        """
        if not product_id:
            return []
        try:
            data = await self.post_json(
                "/internal/order/agent/coPurchaseProductIds",
                {"productId": product_id, "limit": max(1, min(limit, 20))},
            )
            if not data or not isinstance(data, list):
                return []
            return [str(item) for item in data if item]
        except Exception:
            return []

    async def knowledge_version(self) -> int:
        data = await self.post_json("/internal/search/knowledge/version", {})
        try:
            version = int(data)
        except (TypeError, ValueError):
            raise ValueError("invalid knowledge release version response") from None
        if version < 1:
            raise ValueError("knowledge release version must be positive")
        return version

    async def knowledge_catalog(self) -> dict[str, Any]:
        """Return the Java release snapshot used to gate knowledge retrieval.

        The catalog is intentionally validated at the boundary. A partial or
        malformed response must never be treated as an empty catalog, because
        that would make a failed Java request look like a successful archive.
        """
        data = await self.post_json("/internal/search/knowledge/catalog", {})
        if not isinstance(data, dict):
            raise ValueError("invalid knowledge catalog response")
        normalized = normalize_keys(data)
        try:
            version = int(normalized.get("version"))
        except (TypeError, ValueError):
            raise ValueError("knowledge catalog version is invalid") from None
        if version < 1:
            raise ValueError("knowledge catalog version must be positive")
        document_ids = normalized.get("active_document_ids")
        if not isinstance(document_ids, list):
            raise ValueError("knowledge catalog activeDocumentIds is invalid")
        active_ids: list[str] = []
        for document_id in document_ids:
            value = str(document_id).strip() if document_id is not None else ""
            if not value:
                raise ValueError("knowledge catalog contains an empty document id")
            if value not in active_ids:
                active_ids.append(value)
        documents = normalized.get("documents")
        if documents is not None and not isinstance(documents, list):
            raise ValueError("knowledge catalog documents is invalid")
        normalized_documents: list[dict[str, Any]] = []
        for document in documents or []:
            if not isinstance(document, dict):
                raise ValueError("knowledge catalog document is invalid")
            row = normalize_keys(document)
            document_id = str(row.get("document_id") or "").strip()
            source_name = str(row.get("source_name") or "").strip()
            content_hash = str(row.get("content_hash") or "").strip().lower()
            if not document_id or not source_name or len(content_hash) != 64:
                raise ValueError("knowledge catalog document fields are invalid")
            normalized_documents.append(
                {
                    "document_id": document_id,
                    "source_name": source_name,
                    "content_hash": content_hash,
                    "version": int(row.get("version") or 0),
                }
            )
        return {
            "version": version,
            "active_document_ids": active_ids,
            "documents": normalized_documents,
        }

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
