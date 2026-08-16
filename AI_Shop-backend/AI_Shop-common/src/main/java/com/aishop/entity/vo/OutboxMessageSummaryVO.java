package com.aishop.entity.vo;

import com.aishop.entity.po.LocalMessageOutbox;

import java.util.Date;

public record OutboxMessageSummaryVO(
        Long id,
        String idempotencyKey,
        String exchangeName,
        String routingKey,
        String reliabilityLevel,
        Integer retryCount,
        String errorMessage,
        Date createTime,
        Date updateTime) {

    public static OutboxMessageSummaryVO from(LocalMessageOutbox row) {
        return new OutboxMessageSummaryVO(
                row.getId(),
                row.getIdempotencyKey(),
                row.getExchangeName(),
                row.getRoutingKey(),
                row.getReliabilityLevel(),
                row.getRetryCount(),
                row.getErrorMessage(),
                row.getCreateTime(),
                row.getUpdateTime());
    }
}
