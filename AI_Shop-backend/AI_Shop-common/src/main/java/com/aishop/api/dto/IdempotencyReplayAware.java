package com.aishop.api.dto;

public interface IdempotencyReplayAware {

    Boolean getIdempotencyReplayed();

    void setIdempotencyReplayed(Boolean idempotencyReplayed);
}
