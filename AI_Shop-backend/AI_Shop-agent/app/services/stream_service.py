import asyncio
import hashlib
import uuid

from app.constants import (
    DONE,
    ERROR,
    OUTPUTTING,
    WS_MESSAGE_TYPE_AGENT,
)
from app.models.message_send import MessageSendDTO
from app.services.episode_service import current_episode
from app.services.redis_service import redis_service


class StreamService:

    def __init__(self) -> None:
        # Redis normally supplies the counter so multiple workers share an
        # ordering.  Keep a tiny local fallback for startup/tests where Redis
        # is intentionally unavailable; it is not a replay store.
        self._local_sequences: dict[str, int] = {}
        self._local_sequence_lock = asyncio.Lock()

    async def _next_sequence(self, stream_key: str) -> int:
        allocator = getattr(redis_service, "next_ws_stream_sequence", None)
        sequence = await allocator(stream_key) if allocator is not None else None
        if sequence is not None:
            return sequence
        async with self._local_sequence_lock:
            next_value = self._local_sequences.get(stream_key, 0) + 1
            self._local_sequences[stream_key] = next_value
            return next_value

    @staticmethod
    def _context_value(explicit: str | None, context_value: str | None) -> str | None:
        value = explicit or context_value
        normalized = str(value).strip() if value else ""
        return normalized or None

    async def _enrich(
        self,
        dto: MessageSendDTO,
        *,
        run_id: str | None = None,
        request_id: str | None = None,
        episode_id: str | None = None,
        terminal_state: str | None = None,
        replay_cursor: str | None = None,
        seq: int | None = None,
    ) -> MessageSendDTO:
        context = current_episode()
        dto.schema_version = dto.schema_version or 1
        dto.run_id = self._context_value(
            run_id or dto.run_id,
            context.run_id if context else None,
        )
        dto.request_id = self._context_value(
            request_id or dto.request_id,
            context.request_id if context else None,
        )
        dto.episode_id = self._context_value(
            episode_id or dto.episode_id,
            context.episode_id if context else None,
        )
        if dto.run_id and not dto.request_id:
            # Fast paths that run before the worker still get a stable,
            # server-owned correlation value.  Callers with the real request
            # ID can override it through the keyword argument.
            dto.request_id = f"req_{dto.run_id}"
        if dto.run_id and not dto.episode_id:
            dto.episode_id = dto.run_id

        stream_material = ":".join(
            [
                dto.run_id or dto.user_id or "anonymous",
                str(dto.message_id or "unknown"),
            ]
        )
        if seq is not None and int(seq) > 0:
            dto.seq = int(seq)
        elif dto.seq is None:
            dto.seq = await self._next_sequence(stream_material)
        if not dto.event_id:
            # UUID4 is generated once per DTO.  Pub/Sub retries of that DTO
            # therefore retain the same event identity for client dedupe.
            dto.event_id = uuid.uuid4().hex
        if terminal_state is not None:
            dto.terminal_state = terminal_state
        if replay_cursor is not None:
            dto.replay_cursor = replay_cursor
        elif dto.seq is not None:
            # Keep the cursor opaque and free of user/message identifiers.
            digest = hashlib.sha256(stream_material.encode("utf-8")).hexdigest()[:16]
            dto.replay_cursor = f"{digest}:{dto.seq}"
        return dto

    async def _publish(
        self,
        dto: MessageSendDTO,
        *,
        run_id: str | None = None,
        request_id: str | None = None,
        episode_id: str | None = None,
        terminal_state: str | None = None,
        replay_cursor: str | None = None,
        seq: int | None = None,
    ) -> None:

        if not dto.message_type:
            dto.message_type = WS_MESSAGE_TYPE_AGENT
        await self._enrich(
            dto,
            run_id=run_id,
            request_id=request_id,
            episode_id=episode_id,
            terminal_state=terminal_state,
            replay_cursor=replay_cursor,
            seq=seq,
        )
        await redis_service.publish_ws(dto.to_ws_dict())

    async def push_chunk(
        self,
        user_id: str,
        message_id: int,
        content: str,
        user_message: str | None = None,
        *,
        run_id: str | None = None,
        request_id: str | None = None,
        episode_id: str | None = None,
        seq: int | None = None,
    ) -> None:

        dto = MessageSendDTO(
            message_id=message_id,
            user_id=user_id,
            user_message=user_message,
            assistant_message=content,
            out_put_type=OUTPUTTING,
        )
        await self._publish(
            dto,
            run_id=run_id,
            request_id=request_id,
            episode_id=episode_id,
            seq=seq,
        )

    async def push_done(
        self,
        user_id: str,
        message_id: int,
        assistant_message: str = "",
        biz_type: str | None = None,
        user_message: str | None = None,
        source_refs: list[dict] | dict | None = None,
        *,
        run_id: str | None = None,
        request_id: str | None = None,
        episode_id: str | None = None,
        terminal_state: str = "SUCCEEDED",
        replay_cursor: str | None = None,
        seq: int | None = None,
    ) -> None:

        dto = MessageSendDTO(
            message_id=message_id,
            user_id=user_id,
            user_message=user_message,
            assistant_message=assistant_message,
            biz_type=biz_type,
            out_put_type=DONE,
            source_refs=source_refs,
        )
        await self._publish(
            dto,
            run_id=run_id,
            request_id=request_id,
            episode_id=episode_id,
            terminal_state=terminal_state,
            replay_cursor=replay_cursor,
            seq=seq,
        )

    async def push_error(
        self,
        user_id: str,
        message_id: int,
        error: str,
        biz_type: str | None = None,
        *,
        run_id: str | None = None,
        request_id: str | None = None,
        episode_id: str | None = None,
        terminal_state: str = "FAILED",
        replay_cursor: str | None = None,
        seq: int | None = None,
    ) -> None:

        dto = MessageSendDTO(
            message_id=message_id,
            user_id=user_id,
            assistant_message=error,
            biz_type=biz_type,
            out_put_type=ERROR,
        )
        await self._publish(
            dto,
            run_id=run_id,
            request_id=request_id,
            episode_id=episode_id,
            terminal_state=terminal_state,
            replay_cursor=replay_cursor,
            seq=seq,
        )

stream_service = StreamService()
