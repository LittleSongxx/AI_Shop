import pytest

from app.services.agent_runtime import _recover_order_cards, _resolve_product_cards_json


def test_missing_order_cards_are_not_recovered_by_a_second_business_call():
    assert _recover_order_cards(None, ["QUERY_ORDERS"]) is None
    assert _recover_order_cards("[]", ["QUERY_ORDERS"]) == "[]"


@pytest.mark.asyncio
async def test_product_ids_without_cards_do_not_trigger_local_backfill():
    result = await _resolve_product_cards_json(
        None,
        {"productIds": ["product-1"]},
        ["SEARCH_PRODUCTS"],
    )

    assert result == (None, "product_search")
