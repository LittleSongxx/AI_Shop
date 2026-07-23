package com.simlect.service.impl;

import com.simlect.constants.ReliableMessageSender;
import com.simlect.entity.enums.OutboxMessageStatusEnum;
import com.simlect.entity.po.LocalMessageOutbox;
import com.simlect.mappers.LocalMessageOutboxMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Date;

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
}
