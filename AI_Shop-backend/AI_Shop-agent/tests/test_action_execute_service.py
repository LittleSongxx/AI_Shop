from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions import RemoteActionOutcomeUnknown
from app.services.action_execute_service import JavaBridge


class _Response:
    status_code = 200
    is_error = False

    def __init__(self, payload=None, *, json_error: Exception | None = None):
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload

    def raise_for_status(self):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        _Response(json_error=ValueError("truncated JSON")),
        _Response(payload=["unexpected", "array"]),
    ],
)
async def test_invalid_2xx_java_response_is_an_uncertain_write_outcome(response):
    client = AsyncMock()
    client.post.return_value = response
    with patch(
        "app.services.action_execute_service.get_client",
        AsyncMock(return_value=client),
    ):
        with pytest.raises(RemoteActionOutcomeUnknown):
            await JavaBridge()._post_form(
                "user-token",
                "/order/refundOrder",
                {"orderItemId": "item-1"},
                "act_1234567890abcdef1234567890abcdef",
            )
