package com.aishop.biz;

import com.aishop.entity.po.OrderItem;
import com.aishop.entity.po.RefundRequest;
import com.aishop.entity.po.RefundReviewLedger;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.OrderItemMapper;
import com.aishop.mappers.RefundRequestMapper;
import com.aishop.mappers.RefundReviewLedgerMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RefundReviewServiceTest {

    private final RefundRequestMapper refundRequestMapper = mock(RefundRequestMapper.class);
    private final RefundReviewLedgerMapper ledgerMapper = mock(RefundReviewLedgerMapper.class);
    @SuppressWarnings("unchecked")
    private final OrderItemMapper<OrderItem, ?> orderItemMapper = mock(OrderItemMapper.class);
    private RefundReviewService service;

    @BeforeEach
    void setUp() {
        service = new RefundReviewService();
        ReflectionTestUtils.setField(service, "refundRequestMapper", refundRequestMapper);
        ReflectionTestUtils.setField(service, "ledgerMapper", ledgerMapper);
        ReflectionTestUtils.setField(service, "orderItemMapper", orderItemMapper);
    }

    private static RefundRequest manualReview(String refundRequestId, String origin) {
        RefundRequest request = new RefundRequest();
        request.setRefundRequestId(refundRequestId);
        request.setOrderItemId("SMITEM202608050002");
        request.setStatus("MANUAL_REVIEW");
        request.setReviewOriginStatus(origin);
        request.setRefundAmount(new BigDecimal("3999.00"));
        request.setBuyCount(1);
        request.setPropertyValueIdHash("prop-1");
        return request;
    }

    /** 与 manualReview 冻结值一致的正常订单项。 */
    private static OrderItem matchingItem() {
        OrderItem item = new OrderItem();
        item.setOrderItemId("SMITEM202608050002");
        item.setOrderItemStatus(1);
        item.setItemAmount(new BigDecimal("3999.00"));
        item.setBuyCount(1);
        item.setPropertyValueIdHash("prop-1");
        return item;
    }

    private static RefundReviewLedger ledger(String reviewId, String refundRequestId,
                                             String action) {
        RefundReviewLedger ledger = new RefundReviewLedger();
        ledger.setReviewId(reviewId);
        ledger.setRefundRequestId(refundRequestId);
        ledger.setAction(action);
        return ledger;
    }

    /** STOCK_PENDING 恢复场景的订单项：finalizeOrderRefund 已把明细翻为 REFUND(0)。 */
    private static OrderItem refundedItem() {
        OrderItem item = matchingItem();
        item.setOrderItemStatus(0);
        return item;
    }

    @Test
    void approveRestoresPendingPaymentAndClearsRetryState() {
        when(orderItemMapper.selectByOrderItemId("SMITEM202608050002")).thenReturn(matchingItem());
        RefundRequest locked = manualReview("r1", "PENDING_PAYMENT");
        when(refundRequestMapper.selectByIdForUpdate("r1")).thenReturn(locked);
        when(refundRequestMapper.reviewApprove(eq("r1"), eq("PENDING_PAYMENT"))).thenReturn(1);
        when(ledgerMapper.insertIgnore(any())).thenReturn(1);
        RefundRequest restored = new RefundRequest();
        restored.setRefundRequestId("r1");
        restored.setStatus("PENDING_PAYMENT");
        restored.setRetryCount(0);
        when(refundRequestMapper.selectById("r1")).thenReturn(restored);

        RefundRequest result = service.approve("r1", "rv-1", "admin", "渠道已恢复");

        assertSame(restored, result);
        assertEquals("PENDING_PAYMENT", result.getStatus());
        verify(refundRequestMapper).reviewApprove("r1", "PENDING_PAYMENT");
        org.mockito.ArgumentCaptor<RefundReviewLedger> captor =
                org.mockito.ArgumentCaptor.forClass(RefundReviewLedger.class);
        verify(ledgerMapper).insertIgnore(captor.capture());
        assertEquals("APPROVE", captor.getValue().getAction());
        assertEquals("r1", captor.getValue().getRefundRequestId());
    }

    @Test
    void approveRestoresStockPendingOrigin() {
        // 真实场景：资金已退、明细已翻 REFUND(0)——状态校验不适用于该阶段，
        // 审批只恢复库存环节，不被明细状态误拦。
        when(orderItemMapper.selectByOrderItemId("SMITEM202608050002")).thenReturn(refundedItem());
        RefundRequest locked = manualReview("r1", "STOCK_PENDING");
        when(refundRequestMapper.selectByIdForUpdate("r1")).thenReturn(locked);
        when(refundRequestMapper.reviewApprove(eq("r1"), eq("STOCK_PENDING"))).thenReturn(1);
        when(ledgerMapper.insertIgnore(any())).thenReturn(1);
        RefundRequest restored = new RefundRequest();
        restored.setStatus("STOCK_PENDING");
        when(refundRequestMapper.selectById("r1")).thenReturn(restored);

        service.approve("r1", "rv-1", "admin", null);

        verify(refundRequestMapper).reviewApprove("r1", "STOCK_PENDING");
    }

    @Test
    void approveStockPendingSkipsFrozenFieldValidation() {
        // STOCK_PENDING 阶段资金已出：冻结值在此阶段不再变化，漂移校验失去对象，
        // 即使金额/数量与明细不一致也放行（剩余动作只有库存恢复）。
        when(ledgerMapper.selectByReviewId("rv-1")).thenReturn(null);
        RefundRequest locked = manualReview("r1", "STOCK_PENDING");
        when(refundRequestMapper.selectByIdForUpdate("r1")).thenReturn(locked);
        OrderItem drifted = refundedItem();
        drifted.setItemAmount(new BigDecimal("3500.00"));
        when(orderItemMapper.selectByOrderItemId("SMITEM202608050002")).thenReturn(drifted);
        when(refundRequestMapper.reviewApprove(eq("r1"), eq("STOCK_PENDING"))).thenReturn(1);
        when(ledgerMapper.insertIgnore(any())).thenReturn(1);
        when(refundRequestMapper.selectById("r1")).thenReturn(new RefundRequest());

        service.approve("r1", "rv-1", "admin", null);

        verify(refundRequestMapper).reviewApprove("r1", "STOCK_PENDING");
    }

    @Test
    void approveFallsBackToPendingPaymentOnUnknownOrigin() {
        // 旧数据/手工改动导致 origin 缺失时，防御性回到 Saga 起始阶段。
        when(orderItemMapper.selectByOrderItemId("SMITEM202608050002")).thenReturn(matchingItem());
        RefundRequest locked = manualReview("r1", null);
        when(refundRequestMapper.selectByIdForUpdate("r1")).thenReturn(locked);
        when(refundRequestMapper.reviewApprove(eq("r1"), eq("PENDING_PAYMENT"))).thenReturn(1);
        when(ledgerMapper.insertIgnore(any())).thenReturn(1);
        when(refundRequestMapper.selectById("r1")).thenReturn(new RefundRequest());

        service.approve("r1", "rv-1", "admin", null);

        verify(refundRequestMapper).reviewApprove("r1", "PENDING_PAYMENT");
    }

    @Test
    void approveIsIdempotentForReusedReviewId() {
        // 幂等命中时审批早已生效、状态已离开 MANUAL_REVIEW（台账在 CAS 成功后写入）。
        RefundReviewLedger existing = ledger("rv-1", "r1", "APPROVE");
        when(ledgerMapper.selectByReviewId("rv-1")).thenReturn(existing);
        RefundRequest current = manualReview("r1", "PENDING_PAYMENT");
        current.setStatus("COMPLETED");
        when(refundRequestMapper.selectById("r1")).thenReturn(current);

        RefundRequest result = service.approve("r1", "rv-1", "admin", null);

        assertSame(current, result);
        verify(refundRequestMapper, never()).selectByIdForUpdate(anyString());
        verify(refundRequestMapper, never()).reviewApprove(anyString(), anyString());
        verify(ledgerMapper, never()).insertIgnore(any());
    }

    @Test
    void approveRejectsReviewIdReusedForAnotherRequest() {
        RefundReviewLedger existing = ledger("rv-1", "r9", "APPROVE");
        when(ledgerMapper.selectByReviewId("rv-1")).thenReturn(existing);

        BusinessException exc = assertThrows(BusinessException.class,
                () -> service.approve("r1", "rv-1", "admin", null));

        assertEquals("审批编号冲突", exc.getMessage());
    }

    @Test
    void approveRejectsReviewIdReusedForAnotherAction() {
        // 同一幂等键先被 REJECT 用过，调用方再用它发起 APPROVE：拒绝而不是静默成功。
        RefundReviewLedger existing = ledger("rv-1", "r1", "REJECT");
        when(ledgerMapper.selectByReviewId("rv-1")).thenReturn(existing);

        BusinessException exc = assertThrows(BusinessException.class,
                () -> service.approve("r1", "rv-1", "admin", null));

        assertEquals("审批编号已被其他审批动作使用", exc.getMessage());
        verify(refundRequestMapper, never()).selectByIdForUpdate(anyString());
    }

    @Test
    void approveRejectsNonManualReviewState() {
        when(ledgerMapper.selectByReviewId("rv-1")).thenReturn(null);
        RefundRequest locked = manualReview("r1", "PENDING_PAYMENT");
        locked.setStatus("COMPLETED");
        when(refundRequestMapper.selectByIdForUpdate("r1")).thenReturn(locked);

        BusinessException exc = assertThrows(BusinessException.class,
                () -> service.approve("r1", "rv-1", "admin", null));

        assertEquals("退款请求不在人工复核状态", exc.getMessage());
        verify(refundRequestMapper, never()).reviewApprove(anyString(), anyString());
    }

    @Test
    void approveCasFailureSurfacesConflict() {
        when(orderItemMapper.selectByOrderItemId("SMITEM202608050002")).thenReturn(matchingItem());
        when(ledgerMapper.selectByReviewId("rv-1")).thenReturn(null);
        when(refundRequestMapper.selectByIdForUpdate("r1"))
                .thenReturn(manualReview("r1", "PENDING_PAYMENT"));
        when(refundRequestMapper.reviewApprove(anyString(), anyString())).thenReturn(0);

        BusinessException exc = assertThrows(BusinessException.class,
                () -> service.approve("r1", "rv-1", "admin", null));

        assertEquals("审批冲突，请刷新后重试", exc.getMessage());
        verify(ledgerMapper, never()).insertIgnore(any());
    }

    @Test
    void approveCasFailureWithSameReviewIdReturnsCurrent() {
        // 同 review_id 并发竞态：CAS 0 但台账已被对方落下（首次幂等检查未命中，
        // 兜底查询命中）——幂等返回当前状态，不抛冲突。
        when(orderItemMapper.selectByOrderItemId("SMITEM202608050002")).thenReturn(matchingItem());
        when(ledgerMapper.selectByReviewId("rv-1"))
                .thenReturn(null, ledger("rv-1", "r1", "APPROVE"));
        when(refundRequestMapper.selectByIdForUpdate("r1"))
                .thenReturn(manualReview("r1", "PENDING_PAYMENT"));
        when(refundRequestMapper.reviewApprove(anyString(), anyString())).thenReturn(0);
        RefundRequest current = new RefundRequest();
        current.setStatus("COMPLETED");
        when(refundRequestMapper.selectById("r1")).thenReturn(current);

        assertSame(current, service.approve("r1", "rv-1", "admin", null));
        verify(ledgerMapper, never()).insertIgnore(any());
    }

    @Test
    void approveRejectsWhenFrozenAmountDrifted() {
        // 人工复核窗口期订单项改价：冻结金额与当前金额不一致，恢复被拦下。
        when(ledgerMapper.selectByReviewId("rv-1")).thenReturn(null);
        RefundRequest locked = manualReview("r1", "PENDING_PAYMENT");
        when(refundRequestMapper.selectByIdForUpdate("r1")).thenReturn(locked);
        OrderItem drifted = matchingItem();
        drifted.setItemAmount(new BigDecimal("3500.00"));
        when(orderItemMapper.selectByOrderItemId("SMITEM202608050002")).thenReturn(drifted);

        BusinessException exc = assertThrows(BusinessException.class,
                () -> service.approve("r1", "rv-1", "admin", null));

        assertEquals("退款金额与订单项当前金额不一致，请核实后重新审批", exc.getMessage());
        verify(refundRequestMapper, never()).reviewApprove(anyString(), anyString());
    }

    @Test
    void approveRejectsWhenFrozenQuantityDrifted() {
        when(ledgerMapper.selectByReviewId("rv-1")).thenReturn(null);
        RefundRequest locked = manualReview("r1", "PENDING_PAYMENT");
        when(refundRequestMapper.selectByIdForUpdate("r1")).thenReturn(locked);
        OrderItem drifted = matchingItem();
        drifted.setBuyCount(2);
        when(orderItemMapper.selectByOrderItemId("SMITEM202608050002")).thenReturn(drifted);

        BusinessException exc = assertThrows(BusinessException.class,
                () -> service.approve("r1", "rv-1", "admin", null));

        assertEquals("退款数量与订单项当前数量不一致，请核实后重新审批", exc.getMessage());
        verify(refundRequestMapper, never()).reviewApprove(anyString(), anyString());
    }

    @Test
    void approveRejectsWhenPropertyDrifted() {
        when(ledgerMapper.selectByReviewId("rv-1")).thenReturn(null);
        RefundRequest locked = manualReview("r1", "PENDING_PAYMENT");
        when(refundRequestMapper.selectByIdForUpdate("r1")).thenReturn(locked);
        OrderItem drifted = matchingItem();
        drifted.setPropertyValueIdHash("prop-2");
        when(orderItemMapper.selectByOrderItemId("SMITEM202608050002")).thenReturn(drifted);

        BusinessException exc = assertThrows(BusinessException.class,
                () -> service.approve("r1", "rv-1", "admin", null));

        assertEquals("商品属性与订单项当前属性不一致，请核实后重新审批", exc.getMessage());
        verify(refundRequestMapper, never()).reviewApprove(anyString(), anyString());
    }

    @Test
    void approveRejectsWhenItemGoneOrDeparted() {
        when(ledgerMapper.selectByReviewId("rv-1")).thenReturn(null);
        RefundRequest locked = manualReview("r1", "PENDING_PAYMENT");
        when(refundRequestMapper.selectByIdForUpdate("r1")).thenReturn(locked);
        when(orderItemMapper.selectByOrderItemId("SMITEM202608050002")).thenReturn(null);

        assertThrows(BusinessException.class,
                () -> service.approve("r1", "rv-1", "admin", null));

        OrderItem departed = matchingItem();
        departed.setOrderItemStatus(0);
        when(orderItemMapper.selectByOrderItemId("SMITEM202608050002")).thenReturn(departed);
        BusinessException exc = assertThrows(BusinessException.class,
                () -> service.approve("r1", "rv-1", "admin", null));
        assertEquals("订单明细状态已变化，请核实后重新审批", exc.getMessage());
        verify(refundRequestMapper, never()).reviewApprove(anyString(), anyString());
    }

    @Test
    void rejectSkipsFrozenFieldValidation() {
        // 驳回是终止语义，不需要冻结字段校验；订单项查不到也允许驳回。
        when(ledgerMapper.selectByReviewId("rv-1")).thenReturn(null);
        when(refundRequestMapper.selectByIdForUpdate("r1"))
                .thenReturn(manualReview("r1", "PENDING_PAYMENT"));
        when(refundRequestMapper.reviewReject("r1")).thenReturn(1);
        when(ledgerMapper.insertIgnore(any())).thenReturn(1);
        RefundRequest rejected = new RefundRequest();
        rejected.setStatus("REJECTED");
        when(refundRequestMapper.selectById("r1")).thenReturn(rejected);

        service.reject("r1", "rv-1", "admin", "信息有误");

        verify(orderItemMapper, never()).selectByOrderItemId(anyString());
    }

    @Test
    void rejectMarksTerminalState() {
        when(ledgerMapper.selectByReviewId("rv-1")).thenReturn(null);
        when(refundRequestMapper.selectByIdForUpdate("r1"))
                .thenReturn(manualReview("r1", "PENDING_PAYMENT"));
        when(refundRequestMapper.reviewReject("r1")).thenReturn(1);
        when(ledgerMapper.insertIgnore(any())).thenReturn(1);
        RefundRequest rejected = new RefundRequest();
        rejected.setRefundRequestId("r1");
        rejected.setStatus("REJECTED");
        when(refundRequestMapper.selectById("r1")).thenReturn(rejected);

        RefundRequest result = service.reject("r1", "rv-2", "admin", "信息有误");

        assertEquals("REJECTED", result.getStatus());
        verify(refundRequestMapper).reviewReject("r1");
        org.mockito.ArgumentCaptor<RefundReviewLedger> captor =
                org.mockito.ArgumentCaptor.forClass(RefundReviewLedger.class);
        verify(ledgerMapper).insertIgnore(captor.capture());
        assertEquals("REJECT", captor.getValue().getAction());
    }

    @Test
    void rejectIsIdempotentAndCasGuarded() {
        when(ledgerMapper.selectByReviewId("rv-2")).thenReturn(ledger("rv-2", "r1", "REJECT"));
        RefundRequest current = manualReview("r1", "PENDING_PAYMENT");
        current.setStatus("REJECTED");
        when(refundRequestMapper.selectById("r1")).thenReturn(current);

        assertSame(current, service.reject("r1", "rv-2", "admin", null));
        verify(refundRequestMapper, never()).reviewReject(anyString());

        when(ledgerMapper.selectByReviewId("rv-3")).thenReturn(null);
        when(refundRequestMapper.selectByIdForUpdate("r1"))
                .thenReturn(manualReview("r1", "PENDING_PAYMENT"));
        when(refundRequestMapper.reviewReject("r1")).thenReturn(0);
        BusinessException exc = assertThrows(BusinessException.class,
                () -> service.reject("r1", "rv-3", "admin", null));
        assertEquals("审批冲突，请刷新后重试", exc.getMessage());
    }

    @Test
    void listPendingReviewsBoundsLimit() {
        when(refundRequestMapper.selectManualReview(5)).thenReturn(java.util.List.of());
        assertEquals(java.util.List.of(), service.listPendingReviews(5));
        verify(refundRequestMapper).selectManualReview(5);

        service.listPendingReviews(null);
        service.listPendingReviews(500);
        // 缺省与超限都收敛到上限 100。
        verify(refundRequestMapper, org.mockito.Mockito.times(2)).selectManualReview(100);
    }
}
