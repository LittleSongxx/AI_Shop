package com.myshop.service;

import com.myshop.entity.enums.MessageReliabilityLevelEnum;

public interface OutboxMessageService {

    Long savePending(String exchange, String routingKey, Object payload,
                     String idempotencyKey, MessageReliabilityLevelEnum reliabilityLevel);

    void tryDispatch(Long id);

    int dispatchPendingBatch(int batchSize, int maxRetries);
}
