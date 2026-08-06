package com.aishop.biz;

import com.aishop.api.enums.OrderStatusEnum;
import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.po.OrderRequestIdempotency;
import com.aishop.entity.po.RefundRequest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AgentActionStatusServiceTest {

    private static final String USER = "u1";
    private static final String KEY = "act_1234567890abcdef1234567890abcdef";

    @Mock
    private OrderRequestIdempotencyService idempotencyService;
    @Mock
    private OrderInfoService orderInfoService;
    @Mock
    private OrderItemService orderItemService;
    @Mock
    private OrderCommentService orderCommentService;
    @Mock
    private RefundSagaTransactionService refundSagaTransactionService;

    @InjectMocks
    private AgentActionStatusService service;

    @Test
    void completedLedgerIsAuthoritative() {
        OrderRequestIdempotency record = record("COMPLETED");
        when(idempotencyService.find(
                USER,
                OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                KEY)).thenReturn(record);

        Map<String, Object> result = service.resolve(receiptBody());

        assertEquals(AgentActionStatusService.STATUS_SUCCESS, result.get("status"));
        verify(orderInfoService, never()).getOrderInfoByOrderId("o1");
    }

    @Test
    void committedDomainEffectRepairsProcessingLedger() {
        when(idempotencyService.find(
                USER,
                OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                KEY)).thenReturn(record("PROCESSING"));
        OrderInfo order = new OrderInfo();
        order.setOrderId("o1");
        order.setUserId(USER);
        order.setOrderStatus(OrderStatusEnum.COMPLETED.getStatus());
        when(orderInfoService.getOrderInfoByOrderId("o1")).thenReturn(order);

        Map<String, Object> result = service.resolve(receiptBody());

        assertEquals(AgentActionStatusService.STATUS_SUCCESS, result.get("status"));
        verify(idempotencyService).markReconciled(
                USER,
                OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                KEY,
                "订单已确认收货");
    }

    @Test
    void processingLedgerWithoutDomainEvidenceBecomesInconclusive() {
        when(idempotencyService.find(
                USER,
                OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                KEY)).thenReturn(record("PROCESSING"));
        when(orderInfoService.getOrderInfoByOrderId("o1")).thenReturn(null);
        when(idempotencyService.recordInconclusive(
                USER,
                OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                KEY,
                6,
                3600,
                "账本未终结且未观察到领域结果"))
                .thenReturn(record("INCONCLUSIVE"));

        Map<String, Object> result = service.resolve(receiptBody());

        assertEquals(AgentActionStatusService.STATUS_INCONCLUSIVE, result.get("status"));
        verify(idempotencyService, never()).markReconciled(
                USER,
                OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                KEY,
                "订单已确认收货");
    }

    @Test
    void manualReviewLedgerStillChecksDomainButNeverStartsAnotherCommand() {
        when(idempotencyService.find(
                USER,
                OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                KEY)).thenReturn(record("MANUAL_REVIEW"));
        when(orderInfoService.getOrderInfoByOrderId("o1")).thenReturn(null);

        Map<String, Object> result = service.resolve(receiptBody());

        assertEquals(AgentActionStatusService.STATUS_MANUAL_REVIEW, result.get("status"));
        verify(idempotencyService, never()).recordInconclusive(
                USER,
                OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                KEY,
                6,
                3600,
                "账本未终结且未观察到领域结果");
    }

    @Test
    void configuredAttemptBoundaryCanMoveLedgerToManualReview() {
        when(idempotencyService.find(
                USER,
                OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                KEY)).thenReturn(record("INCONCLUSIVE"));
        when(orderInfoService.getOrderInfoByOrderId("o1")).thenReturn(null);
        OrderRequestIdempotency manual = record("MANUAL_REVIEW");
        manual.setReconcileAttempts(1);
        when(idempotencyService.recordInconclusive(
                USER,
                OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                KEY,
                1,
                60,
                "账本未终结且未观察到领域结果"))
                .thenReturn(manual);
        Map<String, Object> body = new java.util.LinkedHashMap<>(receiptBody());
        body.put("maxAttempts", 1);
        body.put("reconcileWindowSeconds", 10);

        Map<String, Object> result = service.resolve(body);

        assertEquals(AgentActionStatusService.STATUS_MANUAL_REVIEW, result.get("status"));
        assertEquals(1, result.get("reconcileAttempts"));
    }

    @Test
    void missingLedgerAndDomainEvidenceIsUnknownRatherThanFailed() {
        when(idempotencyService.find(
                USER,
                OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                KEY)).thenReturn(null);
        when(orderInfoService.getOrderInfoByOrderId("o1")).thenReturn(null);

        Map<String, Object> result = service.resolve(receiptBody());

        assertEquals(AgentActionStatusService.STATUS_UNKNOWN, result.get("status"));
    }

    @Test
    void failedLedgerWithAcceptedRefundRequestIsReconciledAsSuccess() {
        when(idempotencyService.find(
                USER,
                OrderRequestIdempotencyService.COMMAND_AGENT_REFUND,
                KEY)).thenReturn(record("FAILED"));
        RefundRequest request = new RefundRequest();
        request.setUserId(USER);
        request.setStatus("PENDING_PAYMENT");
        when(refundSagaTransactionService.findByOrderItemId("item-1")).thenReturn(request);

        Map<String, Object> result = service.resolve(Map.of(
                "userId", USER,
                "actionType", "REFUND",
                "idempotencyKey", KEY,
                "params", Map.of("orderItemId", "item-1")));

        assertEquals(AgentActionStatusService.STATUS_SUCCESS, result.get("status"));
        verify(idempotencyService).markReconciled(
                USER,
                OrderRequestIdempotencyService.COMMAND_AGENT_REFUND,
                KEY,
                "退款操作已受理");
    }

    private static Map<String, Object> receiptBody() {
        return Map.of(
                "userId", USER,
                "actionType", "CONFIRM_RECEIPT",
                "idempotencyKey", KEY,
                "params", Map.of("orderId", "o1"));
    }

    private static OrderRequestIdempotency record(String status) {
        OrderRequestIdempotency record = new OrderRequestIdempotency();
        record.setStatus(status);
        return record;
    }
}
