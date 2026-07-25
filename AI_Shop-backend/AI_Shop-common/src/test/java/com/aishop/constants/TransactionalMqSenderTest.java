package com.aishop.constants;

import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.service.OutboxMessageService;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionSynchronizationUtils;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class TransactionalMqSenderTest {

    @Mock
    private OutboxMessageService outboxMessageService;
    @Mock
    private ReliableMessageSender reliableMessageSender;
    @InjectMocks
    private TransactionalMqSender sender;

    @AfterEach
    void clearTransactionSynchronization() {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.clearSynchronization();
        }
    }

    @Test
    void outboxIsSavedInTransactionAndDispatchedOnlyAfterCommit() {
        when(outboxMessageService.savePending(
                eq("exchange"), eq("route"), eq("payload"), eq("request-key"),
                eq(MessageReliabilityLevelEnum.HIGH))).thenReturn(41L);
        TransactionSynchronizationManager.initSynchronization();

        sender.sendAfterCommit(
                "exchange", "route", "payload", "request-key", MessageReliabilityLevelEnum.HIGH);

        verify(outboxMessageService).savePending(
                eq("exchange"), eq("route"), eq("payload"), eq("request-key"),
                eq(MessageReliabilityLevelEnum.HIGH));
        verify(outboxMessageService, never()).tryDispatch(any());
        TransactionSynchronizationUtils.invokeAfterCommit(
                TransactionSynchronizationManager.getSynchronizations());
        verify(outboxMessageService).tryDispatch(41L);
    }

    @Test
    void rollbackDoesNotDispatchTheOutboxRecord() {
        when(outboxMessageService.savePending(
                any(), any(), any(), any(), eq(MessageReliabilityLevelEnum.STANDARD))).thenReturn(42L);
        TransactionSynchronizationManager.initSynchronization();

        sender.sendAfterCommit("exchange", "route", "payload", "request-key",
                MessageReliabilityLevelEnum.STANDARD);

        TransactionSynchronizationUtils.invokeAfterCompletion(
                TransactionSynchronizationManager.getSynchronizations(),
                TransactionSynchronization.STATUS_ROLLED_BACK);
        verify(outboxMessageService, never()).tryDispatch(any());
        verify(reliableMessageSender, never()).sendMessage(any(), any(), any(), any(), any());
    }
}
