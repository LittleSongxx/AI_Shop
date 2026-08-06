from unittest.mock import AsyncMock, patch

import pytest

from app.services.stream_service import StreamService


@pytest.mark.asyncio
async def test_done_frame_carries_persisted_source_refs():
    service = StreamService()
    refs = [{"type": "faq", "questionId": 9001, "version": 3}]

    with patch(
        "app.services.stream_service.redis_service.publish_ws", AsyncMock()
    ) as publish:
        await service.push_done("u1", 42, "答案", "faq", "问题", refs)

    payload = publish.await_args.args[0]
    assert payload["messageId"] == "42"
    assert payload["sourceRefs"] == refs
    assert payload["outPutType"] == 1
