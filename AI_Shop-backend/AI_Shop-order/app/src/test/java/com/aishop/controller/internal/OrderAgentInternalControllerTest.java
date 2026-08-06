package com.aishop.controller.internal;

import com.aishop.biz.RefundSagaTransactionService;
import com.aishop.entity.po.RefundRequest;
import com.aishop.entity.vo.ResponseVO;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;
import java.util.Date;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class OrderAgentInternalControllerTest {

    private final RefundSagaTransactionService refundService = mock(RefundSagaTransactionService.class);
    private OrderAgentInternalController controller;

    @BeforeEach
    void setUp() {
        controller = new OrderAgentInternalController();
        ReflectionTestUtils.setField(controller, "refundSagaTransactionService", refundService);
    }

    @Test
    void refundStatusReturnsOnlyPublicFieldsForTheOwner() {
        RefundRequest request = new RefundRequest();
        request.setRefundRequestId("refund-1");
        request.setOrderId("SM202608050002");
        request.setOrderItemId("SMITEM202608050002");
        request.setUserId("u1");
        request.setStatus("COMPLETED");
        request.setRefundAmount(new BigDecimal("3999.00"));
        request.setCreatedAt(new Date());
        request.setCompletedAt(new Date());
        request.setLastError("must not leak");
        when(refundService.findByOrderItemId("SMITEM202608050002")).thenReturn(request);

        ResponseVO<List<Map<String, Object>>> response = controller.refundStatus(Map.of(
                "userId", "u1",
                "orderItemId", "SMITEM202608050002"
        ));

        assertEquals(1, response.getData().size());
        Map<String, Object> data = response.getData().get(0);
        assertEquals("退款已完成", data.get("statusName"));
        assertEquals(new BigDecimal("3999.00"), data.get("refundAmount"));
        assertFalse(data.containsKey("lastError"));
    }

    @Test
    void refundStatusDoesNotRevealAnotherUsersRequest() {
        RefundRequest request = new RefundRequest();
        request.setOrderItemId("SMITEM202608050002");
        request.setUserId("other-user");
        when(refundService.findByOrderItemId("SMITEM202608050002")).thenReturn(request);

        ResponseVO<List<Map<String, Object>>> response = controller.refundStatus(Map.of(
                "userId", "u1",
                "orderItemId", "SMITEM202608050002"
        ));

        assertEquals(List.of(), response.getData());
    }
}
