from pydantic import BaseModel, Field


class MessageSendDTO(BaseModel):
    # Additive stream envelope fields.  Legacy clients ignore unknown fields,
    # while newer clients can correlate, order and reconcile a run without a
    # second event store.
    schema_version: int | None = Field(None, alias="schemaVersion")
    run_id: str | None = Field(None, alias="runId")
    request_id: str | None = Field(None, alias="requestId")
    episode_id: str | None = Field(None, alias="episodeId")
    event_id: str | None = Field(None, alias="eventId")
    seq: int | None = None
    terminal_state: str | None = Field(None, alias="terminalState")
    replay_cursor: str | None = Field(None, alias="replayCursor")
    message_id: int | None = Field(None, alias="messageId")
    user_id: str | None = Field(None, alias="userId")
    user_message: str | None = Field(None, alias="userMessage")
    assistant_message: str | None = Field(None, alias="assistantMessage")
    biz_type: str | None = Field(None, alias="bizType")
    biz_id: str | None = Field(None, alias="bizId")
    message_type: str = Field("agent", alias="messageType")
    out_put_type: int = Field(0, alias="outPutType")
    notification_id: str | None = Field(None, alias="notificationId")
    title: str | None = None
    content: str | None = None
    create_time: str | None = Field(None, alias="createTime")
    source_refs: list[dict] | dict | None = Field(None, alias="sourceRefs")

    model_config = {"populate_by_name": True}

    def to_ws_dict(self) -> dict:
        d = self.model_dump(by_alias=True, exclude_none=True)

        if d.get("messageId") is not None:
            d["messageId"] = str(d["messageId"])
        return d
