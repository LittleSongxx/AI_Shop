package com.aishop.biz;

import com.aishop.api.support.PayFeignSupport;
import com.aishop.api.support.StockFeignSupport;
import com.aishop.entity.po.OrderItem;
import com.aishop.entity.po.RefundRequest;
import com.aishop.exception.BusinessException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RefundSagaServiceTest {

    private final RefundSagaTransactionService transactionService =
            mock(RefundSagaTransactionService.class);
    private final PayFeignSupport payFeignSupport = mock(PayFeignSupport.class);
    private final StockFeignSupport stockFeignSupport = mock(StockFeignSupport.class);
    private RefundSagaService service;

    @BeforeEach
    void setUp() {
        service = new RefundSagaService();
        ReflectionTestUtils.setField(service, "transactionService", transactionService);
        ReflectionTestUtils.setField(service, "payFeignSupport", payFeignSupport);
        ReflectionTestUtils.setField(service, "stockFeignSupport", stockFeignSupport);
    }

    private static OrderItem item() {
        OrderItem item = new OrderItem();
        item.setOrderItemId("SMITEM202608050002");
        return item;
    }

    private static RefundRequest request(String status) {
        RefundRequest request = new RefundRequest();
        request.setRefundRequestId("r1");
        request.setStatus(status);
        request.setUserId("u1");
        return request;
    }

    @Test
    void requestRefundRejectsManualReview() {
        when(transactionService.createOrLoad("SMITEM202608050002", "u1"))
                .thenReturn(request("MANUAL_REVIEW"));

        BusinessException exc = assertThrows(BusinessException.class,
                () -> service.requestRefund(item(), "u1"));

        assertTrue(exc.getMessage().contains("人工复核"));
        verify(transactionService, never()).claimPaymentAttempt(any());
    }

    @Test
    void requestRefundReopensRejectedApplication() {
        // 驳回 = 本次申请作废；用户重新发起 → 重开新申请，重新走全流程。
        when(transactionService.createOrLoad("SMITEM202608050002", "u1"))
                .thenReturn(request("REJECTED"));
        when(transactionService.get("r1")).thenReturn(request("PENDING_PAYMENT"));
        when(transactionService.claimPaymentAttempt("r1")).thenReturn(true);

        service.requestRefund(item(), "u1");

        verify(transactionService).resetRejected("r1");
        verify(transactionService).claimPaymentAttempt("r1");
    }

    @Test
    void reconcileDrivesApprovedRefundToCompletion() throws Exception {
        // B2 恢复链路 e2e：审批通过后 Saga 定时器自动接管，
        // 按原阶段推进 —— 支付 → 库存恢复确认 → 终态 COMPLETED。
        when(transactionService.selectDue(30)).thenReturn(List.of(request("PENDING_PAYMENT")));
        when(transactionService.get("r1")).thenReturn(request("PENDING_PAYMENT"));
        when(transactionService.claimPaymentAttempt("r1")).thenReturn(true);
        when(transactionService.queueStockRestore("r1", false)).thenReturn(true);

        service.reconcile();

        verify(payFeignSupport).refund(
                org.mockito.ArgumentMatchers.nullable(String.class),
                org.mockito.ArgumentMatchers.nullable(String.class),
                org.mockito.ArgumentMatchers.nullable(java.math.BigDecimal.class),
                org.mockito.ArgumentMatchers.nullable(String.class));
        verify(transactionService).markPaymentConfirmed("r1");
        verify(transactionService).queueStockRestore("r1", false);

        // 库存侧确认恢复，reconcile 收敛到完成态。
        when(transactionService.selectDue(30)).thenReturn(List.of(request("STOCK_PENDING")));
        when(stockFeignSupport.isRefundStockApplied("r1")).thenReturn(true);

        service.reconcile();

        verify(transactionService).markCompleted("r1");
    }

    @Test
    void requestRefundProceedsNormallyWhenActive() throws Exception {
        when(transactionService.createOrLoad("SMITEM202608050002", "u1"))
                .thenReturn(request("PENDING_PAYMENT"));
        when(transactionService.get("r1")).thenReturn(request("PENDING_PAYMENT"));
        when(transactionService.claimPaymentAttempt("r1")).thenReturn(true);

        service.requestRefund(item(), "u1");

        verify(payFeignSupport).refund(
                org.mockito.ArgumentMatchers.nullable(String.class),
                org.mockito.ArgumentMatchers.nullable(String.class),
                org.mockito.ArgumentMatchers.nullable(java.math.BigDecimal.class),
                org.mockito.ArgumentMatchers.nullable(String.class));
        verify(transactionService).markPaymentConfirmed("r1");
        verify(transactionService).queueStockRestore("r1", false);
    }
}
