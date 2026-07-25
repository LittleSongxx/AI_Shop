package com.aishop.constants;

import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.service.OutboxMessageService;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Component
@Slf4j
public class TransactionalMqSender {

    @Resource
    private OutboxMessageService outboxMessageService;
    @Resource
    private ReliableMessageSender reliableMessageSender;

    public void sendAfterCommit(String exchange, String routingKey, Object message,
                                String idempotencyKey, MessageReliabilityLevelEnum reliabilityLevel) {
        MessageReliabilityLevelEnum level = reliabilityLevel == null
                ? MessageReliabilityLevelEnum.STANDARD : reliabilityLevel;
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            Long id = outboxMessageService.savePending(exchange, routingKey, message, idempotencyKey, level);
            if (id == null) {
                return;
            }
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    try {
                        outboxMessageService.tryDispatch(id);
                    } catch (Exception e) {
                        log.error("Outbox afterCommit 投递失败，等待定时重试 id={}", id, e);
                    }
                }
            });
            return;
        }
        reliableMessageSender.sendMessage(exchange, routingKey, message, idempotencyKey, level);
    }

    public void sendAfterCommit(Runnable sendAction) {
        if (sendAction == null) {
            return;
        }
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    sendAction.run();
                }
            });
            return;
        }
        sendAction.run();
    }
}
