from unittest.mock import AsyncMock, patch

import pytest

from app.services.episode_service import bind_episode
from app.services.stream_service import StreamService


@pytest.mark.asyncio
async def test_done_frame_carries_persisted_source_refs():
    service = StreamService()
    refs = [{"type": "faq", "questionId": 9001, "version": 3}]

    with (
        patch(
            "app.services.stream_service.redis_service.next_ws_stream_sequence",
            AsyncMock(return_value=1),
        ),
        patch(
            "app.services.stream_service.redis_service.publish_ws", AsyncMock()
        ) as publish,
    ):
        await service.push_done("u1", 42, "答案", "faq", "问题", refs)

    payload = publish.await_args.args[0]
    assert payload["messageId"] == "42"
    assert payload["sourceRefs"] == refs
    assert payload["outPutType"] == 1
    assert payload["schemaVersion"] == 1
    assert payload["seq"] == 1
    assert payload["eventId"]
    assert payload["terminalState"] == "SUCCEEDED"
    assert payload["replayCursor"].endswith(":1")


@pytest.mark.asyncio
async def test_stream_frames_carry_correlation_and_monotonic_sequence():
    service = StreamService()

    with (
        patch(
            "app.services.stream_service.redis_service.next_ws_stream_sequence",
            AsyncMock(side_effect=[4, 5]),
        ),
        patch(
            "app.services.stream_service.redis_service.publish_ws", AsyncMock()
        ) as publish,
    ):
        await service.push_chunk(
            "u1",
            42,
            "第一段",
            run_id="run-1",
            request_id="request-1",
            episode_id="episode-1",
        )
        await service.push_done(
            "u1",
            42,
            "第一段",
            run_id="run-1",
            request_id="request-1",
            episode_id="episode-1",
        )

    chunk, done = [call.args[0] for call in publish.await_args_list]
    assert (chunk["seq"], done["seq"]) == (4, 5)
    assert chunk["runId"] == done["runId"] == "run-1"
    assert chunk["requestId"] == done["requestId"] == "request-1"
    assert chunk["episodeId"] == done["episodeId"] == "episode-1"
    assert chunk["eventId"] != done["eventId"]
    assert "terminalState" not in chunk
    assert done["terminalState"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_stream_frame_inherits_bound_episode_identity():
    service = StreamService()
    with (
        patch(
            "app.services.stream_service.redis_service.next_ws_stream_sequence",
            AsyncMock(return_value=1),
        ),
        patch(
            "app.services.stream_service.redis_service.publish_ws", AsyncMock()
        ) as publish,
        bind_episode(
            "run-bound",
            message_id=43,
            user_id="u1",
            request_id="request-bound",
            episode_id="episode-bound",
        ),
    ):
        await service.push_chunk("u1", 43, "片段")

    payload = publish.await_args.args[0]
    assert payload["runId"] == "run-bound"
    assert payload["requestId"] == "request-bound"
    assert payload["episodeId"] == "episode-bound"
