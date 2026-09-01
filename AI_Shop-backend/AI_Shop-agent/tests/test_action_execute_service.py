from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions import RemoteActionOutcomeUnknown
from app.services.action_execute_service import JavaBridge, action_execute_service


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


@pytest.mark.asyncio
async def test_support_case_confirmation_rebinds_java_owner_header(monkeypatch):
    observed: dict[str, str] = {}

    async def create_from_pending(_pending, _params):
        from app.services.java_internal_client import java_internal_client

        observed.update(java_internal_client._headers())
        return {"caseNo": "SC-1"}

    monkeypatch.setattr(
        "app.services.support_case_service.support_case_service.create_from_pending",
        create_from_pending,
    )

    result = await action_execute_service.execute(
        {
            "actionType": "CREATE_SUPPORT_CASE",
            "userId": "u1",
            "token": "act_1234567890abcdef1234567890abcdef",
            "paramsJson": "{}",
        },
        "ignored-user-token",
    )

    assert result == "售后工单 SC-1 已创建"
    assert observed["X-Agent-User-Id"] == "u1"
