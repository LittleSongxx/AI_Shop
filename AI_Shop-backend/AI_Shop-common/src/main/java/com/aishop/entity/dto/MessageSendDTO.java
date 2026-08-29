package com.aishop.entity.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.databind.annotation.JsonSerialize;
import com.fasterxml.jackson.databind.ser.std.ToStringSerializer;

import java.io.Serializable;

@JsonIgnoreProperties(ignoreUnknown = true)
public class MessageSendDTO<T> implements Serializable {
    private static final long serialVersionUID = -1045752033171142417L;

    /** Optional stream envelope metadata; retained additively for legacy callers. */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    private Integer schemaVersion;

    @JsonInclude(JsonInclude.Include.NON_NULL)
    private String runId;

    @JsonInclude(JsonInclude.Include.NON_NULL)
    private String requestId;

    @JsonInclude(JsonInclude.Include.NON_NULL)
    private String episodeId;

    @JsonInclude(JsonInclude.Include.NON_NULL)
    private String eventId;

    @JsonInclude(JsonInclude.Include.NON_NULL)
    private Long seq;

    @JsonInclude(JsonInclude.Include.NON_NULL)
    private String terminalState;

    @JsonInclude(JsonInclude.Include.NON_NULL)
    private String replayCursor;

    @JsonSerialize(using = ToStringSerializer.class)
    private Integer messageId;

    private String userMessage;

    private String assistantMessage;

    private String bizType;

    private String bizId;

    private String messageType;

    private String notificationId;

    private String title;

    private String content;

    private String createTime;

    private Object sourceRefs;

    private Integer outPutType = 0;

    private String userId;

    public Integer getSchemaVersion() {
        return schemaVersion;
    }

    public void setSchemaVersion(Integer schemaVersion) {
        this.schemaVersion = schemaVersion;
    }

    public String getRunId() {
        return runId;
    }

    public void setRunId(String runId) {
        this.runId = runId;
    }

    public String getRequestId() {
        return requestId;
    }

    public void setRequestId(String requestId) {
        this.requestId = requestId;
    }

    public String getEpisodeId() {
        return episodeId;
    }

    public void setEpisodeId(String episodeId) {
        this.episodeId = episodeId;
    }

    public String getEventId() {
        return eventId;
    }

    public void setEventId(String eventId) {
        this.eventId = eventId;
    }

    public Long getSeq() {
        return seq;
    }

    public void setSeq(Long seq) {
        this.seq = seq;
    }

    public String getTerminalState() {
        return terminalState;
    }

    public void setTerminalState(String terminalState) {
        this.terminalState = terminalState;
    }

    public String getReplayCursor() {
        return replayCursor;
    }

    public void setReplayCursor(String replayCursor) {
        this.replayCursor = replayCursor;
    }

    public String getUserMessage() {
        return userMessage;
    }

    public void setUserMessage(String userMessage) {
        this.userMessage = userMessage;
    }

    public String getAssistantMessage() {
        return assistantMessage;
    }

    public void setAssistantMessage(String assistantMessage) {
        this.assistantMessage = assistantMessage;
    }

    public String getUserId() {
        return userId;
    }

    public void setUserId(String userId) {
        this.userId = userId;
    }

    public Integer getMessageId() {
        return messageId;
    }

    public void setMessageId(Integer messageId) {
        this.messageId = messageId;
    }

    public Integer getOutPutType() {
        return outPutType;
    }

    public void setOutPutType(Integer outPutType) {
        this.outPutType = outPutType;
    }

    public String getBizType() {
        return bizType;
    }

    public void setBizType(String bizType) {
        this.bizType = bizType;
    }

    public String getBizId() {
        return bizId;
    }

    public void setBizId(String bizId) {
        this.bizId = bizId;
    }

    public String getMessageType() {
        return messageType;
    }

    public void setMessageType(String messageType) {
        this.messageType = messageType;
    }

    public String getNotificationId() {
        return notificationId;
    }

    public void setNotificationId(String notificationId) {
        this.notificationId = notificationId;
    }

    public String getTitle() {
        return title;
    }

    public void setTitle(String title) {
        this.title = title;
    }

    public String getContent() {
        return content;
    }

    public void setContent(String content) {
        this.content = content;
    }

    public String getCreateTime() {
        return createTime;
    }

    public void setCreateTime(String createTime) {
        this.createTime = createTime;
    }

    public Object getSourceRefs() {
        return sourceRefs;
    }

    public void setSourceRefs(Object sourceRefs) {
        this.sourceRefs = sourceRefs;
    }
}
