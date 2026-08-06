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
import java.util.Date;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
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
    void processingRequestDoesNotExecuteCommandAgain() {
        Map<String, String> request = Map.of("orderId", "o1");
        OrderRequestIdempotency stored = storedRecord(request, "PROCESSING", null);
        when(mapper.insertProcessing(any())).thenReturn(0);
        when(mapper.selectForUpdate(
                "u1", OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT, KEY))
                .thenReturn(stored);
        AtomicInteger executions = new AtomicInteger();

        HttpBusinessException error = assertThrows(
                HttpBusinessException.class,
                () -> service.execute(
                        "u1",
                        OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                        KEY,
                        request,
                        Map.class,
                        () -> {
                            executions.incrementAndGet();
                            return Map.of();
                        }));

        assertEquals(409, error.getHttpStatus());
        assertEquals("请求正在处理中，请稍后重试", error.getMessage());
        assertEquals(0, executions.get());
    }

    @Test
    void uncertainAndManualReviewRequestsNeverExecuteCommandAgain() {
        Map<String, String> request = Map.of("orderId", "o1");
        for (String status : List.of("INCONCLUSIVE", "MANUAL_REVIEW")) {
            OrderRequestIdempotency stored = storedRecord(request, status, null);
            when(mapper.insertProcessing(any())).thenReturn(0);
            when(mapper.selectForUpdate(
                    "u1", OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT, KEY))
                    .thenReturn(stored);
            AtomicInteger executions = new AtomicInteger();

            HttpBusinessException error = assertThrows(
                    HttpBusinessException.class,
                    () -> service.execute(
                            "u1",
                            OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                            KEY,
                            request,
                            Map.class,
                            () -> {
                                executions.incrementAndGet();
                                return Map.of();
                            }));

            assertEquals(409, error.getHttpStatus());
            assertEquals(0, executions.get());
        }
    }

    @Test
    void reconciliationAttemptUsesBoundedValuesAndReadsBackLedger() {
        OrderRequestIdempotency expected = new OrderRequestIdempotency();
        expected.setStatus("INCONCLUSIVE");
        when(mapper.recordInconclusive(
                anyString(), anyString(), anyString(), anyInt(), any(Date.class), anyString()))
                .thenReturn(1);
        when(mapper.select(
                "u1", OrderRequestIdempotencyService.COMMAND_AGENT_REFUND, KEY))
                .thenReturn(expected);

        OrderRequestIdempotency actual = service.recordInconclusive(
                "u1",
                OrderRequestIdempotencyService.COMMAND_AGENT_REFUND,
                KEY,
                0,
                5,
                "needs review");

        assertEquals(expected, actual);
        verify(mapper).recordInconclusive(
                anyString(), anyString(), anyString(),
                eq(1),
                any(Date.class),
                eq("needs review"));
    }

    @Test
    void failedRequestReplaysStoredFailureWithoutExecutingCommandAgain() {
        Map<String, String> request = Map.of("orderId", "o1");
        OrderRequestIdempotency stored = storedRecord(
                request,
                "FAILED",
                JsonUtils.toJson(Map.of("errorMessage", "订单状态无法确认")));
        when(mapper.insertProcessing(any())).thenReturn(0);
        when(mapper.selectForUpdate(
                "u1", OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT, KEY))
                .thenReturn(stored);
        AtomicInteger executions = new AtomicInteger();

        HttpBusinessException error = assertThrows(
                HttpBusinessException.class,
                () -> service.execute(
                        "u1",
                        OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                        KEY,
                        request,
                        Map.class,
                        () -> {
                            executions.incrementAndGet();
                            return Map.of();
                        }));

        assertEquals(409, error.getHttpStatus());
        assertEquals("原请求执行失败：订单状态无法确认", error.getMessage());
        assertEquals(0, executions.get());
        verify(mapper, never()).markFailed(anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void failedRequestWithDamagedPayloadUsesStableFallback() {
        Map<String, String> request = Map.of("orderId", "o1");
        OrderRequestIdempotency stored = storedRecord(request, "FAILED", "not-json");
        when(mapper.insertProcessing(any())).thenReturn(0);
        when(mapper.selectForUpdate(
                "u1", OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT, KEY))
                .thenReturn(stored);

        HttpBusinessException error = assertThrows(
                HttpBusinessException.class,
                () -> service.execute(
                        "u1",
                        OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                        KEY,
                        request,
                        Map.class,
                        Map::of));

        assertEquals(409, error.getHttpStatus());
        assertEquals("原请求执行失败：操作执行失败", error.getMessage());
    }

    @Test
    void commandFailureMarksProcessingRecordFailed() {
        when(mapper.insertProcessing(any())).thenReturn(1);
        when(mapper.markFailed(anyString(), anyString(), anyString(), anyString()))
                .thenReturn(1);
        HttpBusinessException failure = new HttpBusinessException(600, "订单状态无法确认");

        HttpBusinessException thrown = assertThrows(
                HttpBusinessException.class,
                () -> service.execute(
                        "u1",
                        OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                        KEY,
                        Map.of("orderId", "o1"),
                        Map.class,
                        () -> { throw failure; }));

        assertEquals(failure, thrown);
        verify(mapper).markFailed(
                anyString(), anyString(), anyString(), anyString());
        verify(mapper, never()).markCompleted(anyString(), anyString(), anyString(), anyString());
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

    private static OrderRequestIdempotency storedRecord(
            Object request, String status, String responseJson) {
        OrderRequestIdempotency stored = new OrderRequestIdempotency();
        stored.setRequestHash(RequestFingerprint.sha256(request));
        stored.setStatus(status);
        stored.setResponseJson(responseJson);
        return stored;
    }
}
