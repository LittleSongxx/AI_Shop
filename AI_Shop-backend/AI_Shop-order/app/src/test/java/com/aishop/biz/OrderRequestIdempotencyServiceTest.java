package com.aishop.biz;

import com.aishop.api.dto.PayInfoDTO;
import com.aishop.entity.po.OrderRequestIdempotency;
import com.aishop.exception.HttpBusinessException;
import com.aishop.mappers.OrderRequestIdempotencyMapper;
import com.aishop.utils.JsonUtils;
import com.aishop.utils.RequestFingerprint;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OrderRequestIdempotencyServiceTest {

    private static final String KEY = "order-1234567890abcd";

    @Mock
    private OrderRequestIdempotencyMapper mapper;

    @InjectMocks
    private OrderRequestIdempotencyService service;

    @Test
    void firstRequestExecutesAndPersistsResponse() {
        when(mapper.insertProcessing(any())).thenReturn(1);
        when(mapper.markCompleted(anyString(), anyString(), anyString(), anyString()))
                .thenReturn(1);
        AtomicInteger executions = new AtomicInteger();

        PayInfoDTO result = service.execute(
                "u1",
                OrderRequestIdempotencyService.COMMAND_POST_ORDER,
                KEY,
                Map.of("addressId", "a1"),
                PayInfoDTO.class,
                () -> {
                    executions.incrementAndGet();
                    return payInfo();
                });

        assertEquals(1, executions.get());
        assertFalse(result.getIdempotencyReplayed());
        verify(mapper).markCompleted(anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void sameKeyAndPayloadReplaysStoredResponse() {
        Map<String, String> request = Map.of("addressId", "a1");
        OrderRequestIdempotency stored = new OrderRequestIdempotency();
        stored.setRequestHash(RequestFingerprint.sha256(request));
        stored.setStatus("COMPLETED");
        stored.setResponseJson(JsonUtils.toJson(payInfo()));
        when(mapper.insertProcessing(any())).thenReturn(0);
        when(mapper.selectForUpdate(
                "u1", OrderRequestIdempotencyService.COMMAND_POST_ORDER, KEY))
                .thenReturn(stored);

        PayInfoDTO result = service.execute(
                "u1",
                OrderRequestIdempotencyService.COMMAND_POST_ORDER,
                KEY,
                request,
                PayInfoDTO.class,
                () -> {
                    throw new AssertionError("replayed command must not execute");
                });

        assertTrue(result.getIdempotencyReplayed());
        assertEquals("pay-1", result.getPayOrderId());
        verify(mapper, never()).markCompleted(anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void sameKeyWithDifferentPayloadReturnsConflict() {
        OrderRequestIdempotency stored = new OrderRequestIdempotency();
        stored.setRequestHash(RequestFingerprint.sha256(Map.of("addressId", "a1")));
        stored.setStatus("COMPLETED");
        stored.setResponseJson(JsonUtils.toJson(payInfo()));
        when(mapper.insertProcessing(any())).thenReturn(0);
        when(mapper.selectForUpdate(
                "u1", OrderRequestIdempotencyService.COMMAND_POST_ORDER, KEY))
                .thenReturn(stored);

        HttpBusinessException error = assertThrows(
                HttpBusinessException.class,
                () -> service.execute(
                        "u1",
                        OrderRequestIdempotencyService.COMMAND_POST_ORDER,
                        KEY,
                        Map.of("addressId", "a2"),
                        PayInfoDTO.class,
                        OrderRequestIdempotencyServiceTest::payInfo));

        assertEquals(409, error.getHttpStatus());
    }

    @Test
    void missingOrUnsafeKeyReturnsBadRequest() {
        assertEquals(400, assertThrows(
                HttpBusinessException.class,
                () -> service.validateKey(null)).getHttpStatus());
        assertEquals(400, assertThrows(
                HttpBusinessException.class,
                () -> service.validateKey("short;drop table")).getHttpStatus());
    }

    private static PayInfoDTO payInfo() {
        PayInfoDTO dto = new PayInfoDTO("form", "pay-1", new BigDecimal("10.00"));
        dto.setOrderId("order-1");
        return dto;
    }
}
