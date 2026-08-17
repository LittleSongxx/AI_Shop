package com.aishop.biz.impl;

import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.po.OrderItem;
import com.aishop.entity.query.OrderItemQuery;
import com.aishop.integration.CommerceOutcomeClient;
import com.aishop.mappers.OrderItemMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OrderRepeatPurchaseOutcomeTest {

    @Mock
    private OrderItemMapper<OrderItem, OrderItemQuery> orderItemMapper;
    @Mock
    private CommerceOutcomeClient commerceOutcomeClient;

    private OrderInfoServiceImpl service;

    @BeforeEach
    void setUp() {
        service = new OrderInfoServiceImpl();
        ReflectionTestUtils.setField(service, "orderItemMapper", orderItemMapper);
        ReflectionTestUtils.setField(service, "commerceOutcomeClient", commerceOutcomeClient);
    }

    @Test
    void historicalSuccessfulProductAddsIdempotentRepeatPurchaseOutcome() {
        OrderInfo order = order("order-current", "user-1", "199.00");
        OrderItem item = item("item-current", "order-current", "product-1", "199.00");
        when(orderItemMapper.selectList(any(OrderItemQuery.class))).thenReturn(List.of(item));
        when(orderItemMapper.countPriorSuccessfulPurchases(
                eq("user-1"), eq("product-1"), anyList(), anyList()))
                .thenReturn(1);

        ReflectionTestUtils.invokeMethod(
                service, "recordPaymentOutcomes", List.of(order), "pay-current");

        ArgumentCaptor<CommerceOutcomeClient.OutcomeEvent> events =
                ArgumentCaptor.forClass(CommerceOutcomeClient.OutcomeEvent.class);
        verify(commerceOutcomeClient, times(2)).recordAfterCommit(events.capture());
        assertEquals(List.of("PAYMENT", "REPEAT_PURCHASE"), events.getAllValues().stream()
                .map(CommerceOutcomeClient.OutcomeEvent::eventType)
                .toList());
        CommerceOutcomeClient.OutcomeEvent repeat = events.getAllValues().get(1);
        assertEquals("ORDER", repeat.source());
        assertEquals("product-1", repeat.productId());
        assertEquals("order-current", repeat.orderId());
        assertEquals(new BigDecimal("199.00"), repeat.payload().get("paidAmount"));
        assertNull(repeat.requestId());
        verify(orderItemMapper).countPriorSuccessfulPurchases(
                eq("user-1"), eq("product-1"), eq(List.of("order-current")), anyList());
    }

    @Test
    void firstSuccessfulProductOnlyRecordsPaymentOutcome() {
        OrderInfo order = order("order-first", "user-1", "99.00");
        OrderItem item = item("item-first", "order-first", "product-2", "99.00");
        when(orderItemMapper.selectList(any(OrderItemQuery.class))).thenReturn(List.of(item));
        when(orderItemMapper.countPriorSuccessfulPurchases(
                eq("user-1"), eq("product-2"), anyList(), anyList()))
                .thenReturn(0);

        ReflectionTestUtils.invokeMethod(
                service, "recordPaymentOutcomes", List.of(order), "pay-first");

        ArgumentCaptor<CommerceOutcomeClient.OutcomeEvent> event =
                ArgumentCaptor.forClass(CommerceOutcomeClient.OutcomeEvent.class);
        verify(commerceOutcomeClient).recordAfterCommit(event.capture());
        assertEquals("PAYMENT", event.getValue().eventType());
    }

    private static OrderInfo order(String orderId, String userId, String amount) {
        OrderInfo order = new OrderInfo();
        order.setOrderId(orderId);
        order.setUserId(userId);
        order.setAmount(new BigDecimal(amount));
        return order;
    }

    private static OrderItem item(
            String itemId, String orderId, String productId, String amount) {
        OrderItem item = new OrderItem();
        item.setOrderItemId(itemId);
        item.setOrderId(orderId);
        item.setProductId(productId);
        item.setPropertyValueIdHash("sku-1");
        item.setBuyCount(1);
        item.setItemAmount(new BigDecimal(amount));
        return item;
    }
}
