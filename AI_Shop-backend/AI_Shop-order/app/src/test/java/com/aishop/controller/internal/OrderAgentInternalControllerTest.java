package com.aishop.controller.internal;

import com.aishop.api.enums.OrderCommentStatusEnum;
import com.aishop.biz.OrderInfoService;
import com.aishop.biz.OrderItemService;
import com.aishop.biz.RefundSagaTransactionService;
import com.aishop.constants.InternalApiHeaders;
import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.po.OrderItem;
import com.aishop.entity.po.RefundRequest;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.exception.BusinessException;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import java.math.BigDecimal;
import java.util.Date;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class OrderAgentInternalControllerTest {

    private final RefundSagaTransactionService refundService = mock(RefundSagaTransactionService.class);
    private final OrderInfoService orderInfoService = mock(OrderInfoService.class);
    private final OrderItemService orderItemService = mock(OrderItemService.class);
    private OrderAgentInternalController controller;

    @BeforeEach
    void setUp() {
        controller = new OrderAgentInternalController();
        ReflectionTestUtils.setField(controller, "refundSagaTransactionService", refundService);
        ReflectionTestUtils.setField(controller, "orderInfoService", orderInfoService);
        ReflectionTestUtils.setField(controller, "orderItemService", orderItemService);
    }

    @AfterEach
    void tearDown() {
        RequestContextHolder.resetRequestAttributes();
    }

    private void delegateAs(String userId) {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader(InternalApiHeaders.AGENT_USER_ID, userId);
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));
    }

    @Test
    void refundStatusReturnsOnlyPublicFieldsForTheOwner() {
        delegateAs("u1");
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
    void refundStatusShowsRejectedStatusName() {
        delegateAs("u1");
        RefundRequest request = new RefundRequest();
        request.setRefundRequestId("refund-1");
        request.setOrderId("SM202608050002");
        request.setOrderItemId("SMITEM202608050002");
        request.setUserId("u1");
        request.setStatus("REJECTED");
        request.setRefundAmount(new BigDecimal("3999.00"));
        request.setCreatedAt(new Date());
        when(refundService.findByOrderItemId("SMITEM202608050002")).thenReturn(request);

        ResponseVO<List<Map<String, Object>>> response = controller.refundStatus(Map.of(
                "userId", "u1",
                "orderItemId", "SMITEM202608050002"
        ));

        assertEquals("退款申请已驳回", response.getData().get(0).get("statusName"));
    }

    @Test
    void refundStatusDoesNotRevealAnotherUsersRequest() {
        delegateAs("u1");
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

    @Test
    void refundStatusRejectsBodyUserIdMismatchingDelegation() {
        // 模型在 body 里把身份换成本人之外的用户：委托头是权威，必须拒绝。
        delegateAs("u1");
        BusinessException exc = assertThrows(BusinessException.class,
                () -> controller.refundStatus(Map.of(
                        "userId", "u2",
                        "orderItemId", "SMITEM202608050002"
                )));
        assertEquals(403, exc.getCode());
    }

    @Test
    void refundStatusRejectsMissingDelegationHeader() {
        // fail-closed：带用户数据的接口没有委托头时不能退化为旧信任。
        BusinessException exc = assertThrows(BusinessException.class,
                () -> controller.refundStatus(Map.of(
                        "userId", "u1",
                        "orderItemId", "SMITEM202608050002"
                )));
        assertEquals(401, exc.getCode());
    }

    @Test
    void getOrderReturnsOwnersOrder() {
        delegateAs("u1");
        OrderInfo order = new OrderInfo();
        order.setOrderId("SM202608050001");
        order.setUserId("u1");
        order.setOrderStatus(3);
        when(orderInfoService.getOrderInfoByOrderId("SM202608050001")).thenReturn(order);
        when(orderItemService.findListByParam(org.mockito.ArgumentMatchers.any())).thenReturn(List.of());

        ResponseVO<Map<String, Object>> response =
                controller.getOrder(Map.of("orderId", "SM202608050001"));

        assertEquals("SM202608050001", response.getData().get("orderId"));
    }

    @Test
    void getOrderRejectsAnotherUsersOrder() {
        delegateAs("u1");
        OrderInfo order = new OrderInfo();
        order.setOrderId("SM202608050001");
        order.setUserId("other-user");
        when(orderInfoService.getOrderInfoByOrderId("SM202608050001")).thenReturn(order);

        BusinessException exc = assertThrows(BusinessException.class,
                () -> controller.getOrder(Map.of("orderId", "SM202608050001")));
        assertEquals(403, exc.getCode());
    }

    @Test
    void getOrderItemRejectsOrderOfAnotherUser() {
        delegateAs("u1");
        OrderItem item = new OrderItem();
        item.setOrderItemId("SMITEM202608050001");
        item.setOrderId("SM202608050001");
        item.setProductId("p1");
        when(orderItemService.getOrderItemByOrderItemId("SMITEM202608050001")).thenReturn(item);
        OrderInfo order = new OrderInfo();
        order.setUserId("other-user");
        when(orderInfoService.getOrderInfoByOrderId("SM202608050001")).thenReturn(order);

        BusinessException exc = assertThrows(BusinessException.class,
                () -> controller.getOrderItem(Map.of("orderItemId", "SMITEM202608050001")));
        assertEquals(403, exc.getCode());
    }

    @Test
    void listOrderItemsRejectsMissingOrder() {
        delegateAs("u1");
        when(orderInfoService.getOrderInfoByOrderId(anyString())).thenReturn(null);

        BusinessException exc = assertThrows(BusinessException.class,
                () -> controller.listOrderItems(Map.of("orderId", "SM202608059999")));
        assertEquals(403, exc.getCode());
    }

    @Test
    void listOrdersRequiresMatchingDelegation() {
        delegateAs("u1");
        BusinessException exc = assertThrows(BusinessException.class,
                () -> controller.listOrders(Map.of("userId", "u9")));
        assertEquals(403, exc.getCode());
    }

    @Test
    void actionCapabilityAllowsCancelableOwnedOrder() {
        delegateAs("u1");
        OrderInfo order = new OrderInfo();
        order.setOrderId("SM202608050001");
        order.setUserId("u1");
        order.setOrderStatus(0);
        when(orderInfoService.getOrderInfoByOrderId("SM202608050001"))
                .thenReturn(order);

        ResponseVO<Map<String, Object>> response = controller.actionCapability(Map.of(
                "action", "CANCEL_ORDER",
                "orderId", "SM202608050001"
        ));

        assertEquals("ALLOWED", response.getData().get("decision"));
        assertEquals("CANCEL_ORDER", response.getData().get("action"));
        assertEquals("order-action-capability/v1",
                response.getData().get("capabilityVersion"));
        assertEquals("order-action-snapshot/v1",
                response.getData().get("snapshotVersion"));
        assertTrue(String.valueOf(response.getData().get("snapshotEtag"))
                .matches("sha256:[0-9a-f]{64}"));
        assertEquals(response.getData().get("snapshotEtag"),
                response.getData().get("snapshotETag"));
        assertEquals(0, ((Map<?, ?>) response.getData().get("snapshot"))
                .get("orderStatus"));

        String firstEtag = String.valueOf(response.getData().get("snapshotEtag"));
        order.setOrderStatus(1);
        ResponseVO<Map<String, Object>> changed = controller.actionCapability(Map.of(
                "action", "CANCEL_ORDER",
                "orderId", "SM202608050001"
        ));
        assertNotEquals(firstEtag, changed.getData().get("snapshotEtag"));
    }

    @Test
    void actionCapabilityDeniesByCurrentBusinessState() {
        delegateAs("u1");
        OrderInfo order = new OrderInfo();
        order.setOrderId("SM202608050001");
        order.setUserId("u1");
        order.setOrderStatus(1);
        when(orderInfoService.getOrderInfoByOrderId("SM202608050001"))
                .thenReturn(order);

        ResponseVO<Map<String, Object>> response = controller.actionCapability(Map.of(
                "action", "CANCEL_ORDER",
                "orderId", "SM202608050001"
        ));

        assertEquals("DENIED", response.getData().get("decision"));
        assertEquals("ORDER_STATUS_NOT_CANCELLABLE",
                response.getData().get("reasonCode"));
    }

    @Test
    void actionCapabilityAllowsFirstReviewOnlyBeforeEvaluation() {
        delegateAs("u1");
        OrderInfo order = new OrderInfo();
        order.setOrderId("SM202608050001");
        order.setUserId("u1");
        order.setCommentStatus(OrderCommentStatusEnum.NOT_EVALUATED.getStatus());
        when(orderInfoService.getOrderInfoByOrderId("SM202608050001"))
                .thenReturn(order);

        ResponseVO<Map<String, Object>> allowed = controller.actionCapability(Map.of(
                "action", "PRODUCT_REVIEW",
                "orderId", "SM202608050001"
        ));
        order.setCommentStatus(OrderCommentStatusEnum.EVALUATED.getStatus());
        ResponseVO<Map<String, Object>> denied = controller.actionCapability(Map.of(
                "action", "PRODUCT_REVIEW",
                "orderId", "SM202608050001"
        ));

        assertEquals("ALLOWED", allowed.getData().get("decision"));
        assertEquals("DENIED", denied.getData().get("decision"));
        assertEquals("COMMENT_ALREADY_EVALUATED",
                denied.getData().get("reasonCode"));
    }

    @Test
    void actionCapabilityAllowsRecommentOnlyAfterFirstEvaluation() {
        delegateAs("u1");
        OrderInfo order = new OrderInfo();
        order.setOrderId("SM202608050001");
        order.setUserId("u1");
        order.setCommentStatus(OrderCommentStatusEnum.NOT_EVALUATED.getStatus());
        when(orderInfoService.getOrderInfoByOrderId("SM202608050001"))
                .thenReturn(order);

        ResponseVO<Map<String, Object>> denied = controller.actionCapability(Map.of(
                "action", "RECOMMENT",
                "orderId", "SM202608050001"
        ));
        order.setCommentStatus(OrderCommentStatusEnum.EVALUATED.getStatus());
        ResponseVO<Map<String, Object>> allowed = controller.actionCapability(Map.of(
                "action", "RECOMMENT",
                "orderId", "SM202608050001"
        ));

        assertEquals("DENIED", denied.getData().get("decision"));
        assertEquals("COMMENT_NOT_RECOMMENTABLE",
                denied.getData().get("reasonCode"));
        assertEquals("ALLOWED", allowed.getData().get("decision"));
    }

    @Test
    void actionCapabilityIgnoresModelVisibleUserIdAndUsesDelegation() {
        delegateAs("u1");
        OrderInfo order = new OrderInfo();
        order.setOrderId("SM202608050001");
        order.setUserId("u1");
        order.setOrderStatus(2);
        when(orderInfoService.getOrderInfoByOrderId("SM202608050001"))
                .thenReturn(order);

        ResponseVO<Map<String, Object>> response = controller.actionCapability(Map.of(
                "action", "CONFIRM_RECEIPT",
                "orderId", "SM202608050001",
                "userId", "attacker-controlled"
        ));

        assertEquals("ALLOWED", response.getData().get("decision"));
    }

    @Test
    void actionCapabilityRejectsAnotherUsersOrder() {
        delegateAs("u1");
        OrderInfo order = new OrderInfo();
        order.setOrderId("SM202608050001");
        order.setUserId("other-user");
        when(orderInfoService.getOrderInfoByOrderId("SM202608050001"))
                .thenReturn(order);

        BusinessException exc = assertThrows(BusinessException.class,
                () -> controller.actionCapability(Map.of(
                        "action", "CANCEL_ORDER",
                        "orderId", "SM202608050001"
                )));
        assertEquals(403, exc.getCode());
    }

    @Test
    void actionCapabilityRejectsItemFromAnotherOrder() {
        delegateAs("u1");
        OrderInfo order = new OrderInfo();
        order.setOrderId("SM202608050001");
        order.setUserId("u1");
        when(orderInfoService.getOrderInfoByOrderId("SM202608050001"))
                .thenReturn(order);
        OrderItem item = new OrderItem();
        item.setOrderItemId("SMITEM202608050009");
        item.setOrderId("SM202608050009");
        when(orderItemService.getOrderItemByOrderItemId("SMITEM202608050009"))
                .thenReturn(item);

        ResponseVO<Map<String, Object>> response = controller.actionCapability(Map.of(
                "action", "CANCEL_ORDER",
                "orderId", "SM202608050001",
                "orderItemId", "SMITEM202608050009"
        ));

        assertEquals("DENIED", response.getData().get("decision"));
        assertEquals("ORDER_ITEM_MISMATCH", response.getData().get("reasonCode"));
    }

    @Test
    void actionCapabilityReturnsUnavailableForUnknownAction() {
        delegateAs("u1");
        OrderInfo order = new OrderInfo();
        order.setOrderId("SM202608050001");
        order.setUserId("u1");
        when(orderInfoService.getOrderInfoByOrderId("SM202608050001"))
                .thenReturn(order);

        ResponseVO<Map<String, Object>> response = controller.actionCapability(Map.of(
                "action", "DROP_DATABASE",
                "orderId", "SM202608050001"
        ));

        assertEquals("UNAVAILABLE", response.getData().get("decision"));
        assertEquals("UNSUPPORTED_ACTION", response.getData().get("reasonCode"));
    }
}
