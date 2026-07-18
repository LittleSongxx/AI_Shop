from datetime import datetime, timedelta

from app.constants import ORDER_ITEM_STATUS_NORMAL, REFUNDABLE_ORDER_STATUSES
from app.services.java_internal_client import java_internal_client
from app.utils.biz_payload import build_order_payload

class OrderService:

    async def query_orders(self, user_id: str, order_id: str | None = None) -> tuple[str, str | None, str]:

        if order_id:
            orders = await self._fetch_orders(user_id, order_id=order_id)
        else:
            end = datetime.now()
            start = end - timedelta(days=15)
            orders = await self._fetch_orders(
                user_id,
                time_start=start.strftime("%Y-%m-%d 00:00:00"),
                time_end=end.strftime("%Y-%m-%d %H:%M:%S"),
            )
        if not orders:
            return "[]", None, "query_order"
        items_map = self._items_map_from_orders(orders)

        if not items_map:
            order_ids = [str(o["order_id"]) for o in orders if o.get("order_id")]
            items_map = await self._fetch_order_items(order_ids)
        assistant, biz_data = build_order_payload(orders, items_map)
        return assistant, biz_data, "query_order"

    async def _fetch_orders(
        self,
        user_id: str,
        order_id: str | None = None,
        time_start: str | None = None,
        time_end: str | None = None,
    ) -> list[dict]:

        return await java_internal_client.list_orders(
            user_id=user_id,
            order_id=order_id,
            time_start=time_start,
            time_end=time_end,
            limit=30,
        )

    @staticmethod
    def _items_map_from_orders(orders: list[dict]) -> dict[str, list[dict]]:

        result: dict[str, list[dict]] = {}
        for o in orders:
            oid = o.get("order_id")
            items = o.get("items") or o.get("order_item_list") or []
            if oid and items:
                result[str(oid)] = items
        return result

    async def _fetch_order_items(self, order_ids: list[str]) -> dict[str, list[dict]]:

        if not order_ids:
            return {}
        result: dict[str, list[dict]] = {}
        for oid in order_ids:
            rows = await java_internal_client.list_order_items(oid)
            if rows:
                result[str(oid)] = rows
        return result

    async def get_order(self, order_id: str) -> dict | None:

        return await java_internal_client.get_order(order_id)

    async def get_order_item(self, order_item_id: str) -> dict | None:

        return await java_internal_client.get_order_item(order_item_id)

    async def list_order_items(self, order_id: str) -> list[dict]:

        return await java_internal_client.list_order_items(order_id)

    async def list_refundable_items(self, user_id: str, order_id: str) -> list[dict]:

        order = await self.get_order(order_id)
        if not order or order.get("user_id") != user_id:
            return []
        if order.get("order_status") not in REFUNDABLE_ORDER_STATUSES:
            return []
        items = await self.list_order_items(order_id)
        return [i for i in items if i.get("order_item_status") == ORDER_ITEM_STATUS_NORMAL]

order_service = OrderService()
