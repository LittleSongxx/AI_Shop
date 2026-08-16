package com.aishop.service.impl;

import com.aishop.constants.ReliableMessageSender;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.enums.OutboxMessageStatusEnum;
import com.aishop.entity.po.LocalMessageOutbox;
import com.aishop.mappers.LocalMessageOutboxMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OutboxMessageServiceImplTest {

    @Mock
    private LocalMessageOutboxMapper mapper;
    @Mock
    private ReliableMessageSender sender;
    @InjectMocks
    private OutboxMessageServiceImpl service;

    @BeforeEach
    void setUp() {
        ReflectionTestUtils.setField(service, "leaseMs", 30_000L);
        ReflectionTestUtils.setField(service, "retryBaseMs", 10L);
    }

    @Test
    void expiredSendingLeaseIsReclaimedAndConfirmedBeforeSent() {
        LocalMessageOutbox row = new LocalMessageOutbox();
        row.setId(7L);
        row.setStatus(OutboxMessageStatusEnum.SENDING.getStatus());
        row.setExchangeName("test.exchange");
        row.setRoutingKey("test.key");
        row.setIdempotencyKey("test:7");
        row.setPayloadJson("{\"id\":7}");
        row.setRetryCount(1);
        row.setLeaseUntil(new Date(System.currentTimeMillis() - 1_000));

        when(mapper.selectById(7L)).thenReturn(row);
        when(mapper.claimForDispatch(
                eq(7L), anyInt(), anyInt(), anyInt(), anyString(), any(Date.class), any(Date.class)))
                .thenReturn(1);
        when(mapper.markSent(
                eq(7L), anyInt(), anyInt(), anyString(), any(Date.class)))
                .thenReturn(1);

        service.tryDispatch(7L);

        verify(sender).replaySend(
                eq("test.exchange"), eq("test.key"), any(), eq("test:7"));
        verify(mapper).markSent(
                eq(7L), eq(OutboxMessageStatusEnum.SENDING.getStatus()),
                eq(OutboxMessageStatusEnum.SENT.getStatus()), anyString(), any(Date.class));
    }

    @Test
    void dispatchBatchPassesRetryCapToDatabaseQuery() {
        when(mapper.markRetriesExhausted(
                eq(OutboxMessageStatusEnum.FAILED.getStatus()),
                eq(OutboxMessageStatusEnum.SENDING.getStatus()),
                eq(OutboxMessageStatusEnum.EXHAUSTED.getStatus()),
                eq(10),
                any(Date.class)))
                .thenReturn(0);
        when(mapper.selectDispatchBatch(
                eq(OutboxMessageStatusEnum.PENDING.getStatus()),
                eq(OutboxMessageStatusEnum.FAILED.getStatus()),
                eq(OutboxMessageStatusEnum.SENDING.getStatus()),
                any(Date.class),
                any(Date.class),
                eq(10),
                eq(20)))
                .thenReturn(List.of());

        service.dispatchPendingBatch(20, 10);

        verify(mapper).markRetriesExhausted(
                eq(OutboxMessageStatusEnum.FAILED.getStatus()),
                eq(OutboxMessageStatusEnum.SENDING.getStatus()),
                eq(OutboxMessageStatusEnum.EXHAUSTED.getStatus()),
                eq(10),
                any(Date.class));
        verify(mapper).selectDispatchBatch(
                eq(OutboxMessageStatusEnum.PENDING.getStatus()),
                eq(OutboxMessageStatusEnum.FAILED.getStatus()),
                eq(OutboxMessageStatusEnum.SENDING.getStatus()),
                any(Date.class),
                any(Date.class),
                eq(10),
                eq(20));
    }

    @Test
    void exhaustedMessageCanBeRequeuedAndDispatched() {
        LocalMessageOutbox row = new LocalMessageOutbox();
        row.setId(12L);
        row.setStatus(OutboxMessageStatusEnum.PENDING.getStatus());
        row.setExchangeName("test.exchange");
        row.setRoutingKey("test.key");
        row.setIdempotencyKey("test:12");
        row.setPayloadJson("{\"id\":12}");
        row.setRetryCount(0);

        when(mapper.requeueExhausted(
                12L,
                OutboxMessageStatusEnum.EXHAUSTED.getStatus(),
                OutboxMessageStatusEnum.PENDING.getStatus()))
                .thenReturn(1);
        when(mapper.selectById(12L)).thenReturn(row);
        when(mapper.claimForDispatch(
                eq(12L), anyInt(), anyInt(), anyInt(), anyString(), any(Date.class), any(Date.class)))
                .thenReturn(1);
        when(mapper.markSent(
                eq(12L), anyInt(), anyInt(), anyString(), any(Date.class)))
                .thenReturn(1);

        assertEquals(true, service.replayExhausted(12L));

        verify(sender).replaySend(
                eq("test.exchange"), eq("test.key"), any(), eq("test:12"));
    }

    @Test
    void exhaustedCountUsesExplicitTerminalStatus() {
        when(mapper.countByStatus(OutboxMessageStatusEnum.EXHAUSTED.getStatus()))
                .thenReturn(3);

        assertEquals(3, service.countExhausted());
    }

    @Test
    void existingIdempotencyKeyAcceptsTheSameSemanticMessage() {
        LocalMessageOutbox existing = existingMessage("{\"a\":1,\"b\":2}");
        when(mapper.selectByIdempotencyKey("same-key")).thenReturn(existing);
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("b", 2);
        payload.put("a", 1);

        Long id = service.savePending(
                "test.exchange",
                "test.key",
                payload,
                "same-key",
                MessageReliabilityLevelEnum.STANDARD);

        assertEquals(7L, id);
    }

    @Test
    void existingIdempotencyKeyRejectsDifferentPayload() {
        when(mapper.selectByIdempotencyKey("same-key"))
                .thenReturn(existingMessage("{\"id\":7}"));

        assertThrows(IllegalStateException.class, () -> service.savePending(
                "test.exchange",
                "test.key",
                Map.of("id", 8),
                "same-key",
                MessageReliabilityLevelEnum.STANDARD));
    }

    @Test
    void existingIdempotencyKeyRejectsDifferentRoute() {
        when(mapper.selectByIdempotencyKey("same-key"))
                .thenReturn(existingMessage("{\"id\":7}"));

        assertThrows(IllegalStateException.class, () -> service.savePending(
                "other.exchange",
                "test.key",
                Map.of("id", 7),
                "same-key",
                MessageReliabilityLevelEnum.STANDARD));
    }

    private static LocalMessageOutbox existingMessage(String payloadJson) {
        LocalMessageOutbox row = new LocalMessageOutbox();
        row.setId(7L);
        row.setIdempotencyKey("same-key");
        row.setExchangeName("test.exchange");
        row.setRoutingKey("test.key");
        row.setPayloadJson(payloadJson);
        row.setReliabilityLevel(MessageReliabilityLevelEnum.STANDARD.getCode());
        return row;
    }
}
