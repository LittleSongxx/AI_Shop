from app.constants import DONE, ERROR, OUTPUTTING, WS_MESSAGE_TYPE_AGENT
from app.models.message_send import MessageSendDTO
from app.services.redis_service import redis_service


class StreamService:

    async def _publish(self, dto: MessageSendDTO) -> None:

        if not dto.message_type:
            dto.message_type = WS_MESSAGE_TYPE_AGENT
        await redis_service.publish_ws(dto.to_ws_dict())

    async def push_chunk(
        self,
        user_id: str,
        message_id: int,
        content: str,
        user_message: str | None = None,
    ) -> None:

        dto = MessageSendDTO(
            message_id=message_id,
            user_id=user_id,
            user_message=user_message,
            assistant_message=content,
            out_put_type=OUTPUTTING,
        )
        await self._publish(dto)

    async def push_done(
        self,
        user_id: str,
        message_id: int,
        assistant_message: str = "",
        biz_type: str | None = None,
        user_message: str | None = None,
    ) -> None:

        dto = MessageSendDTO(
            message_id=message_id,
            user_id=user_id,
            user_message=user_message,
            assistant_message=assistant_message,
            biz_type=biz_type,
            out_put_type=DONE,
        )
        await self._publish(dto)

    async def push_error(
        self,
        user_id: str,
        message_id: int,
        error: str,
        biz_type: str | None = None,
    ) -> None:

        dto = MessageSendDTO(
            message_id=message_id,
            user_id=user_id,
            assistant_message=error,
            biz_type=biz_type,
            out_put_type=ERROR,
        )
        await self._publish(dto)

stream_service = StreamService()
