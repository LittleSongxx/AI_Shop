package com.aishop.service;

import com.aishop.entity.enums.MessageReliabilityLevelEnum;

public interface OutboxMessageService {

    Long savePending(String exchange, String routingKey, Object payload,
                     String idempotencyKey, MessageReliabilityLevelEnum reliabilityLevel);

    void tryDispatch(Long id);

    int dispatchPendingBatch(int batchSize, int maxRetries);
}
