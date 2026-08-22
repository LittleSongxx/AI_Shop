package com.aishop.biz.impl;

import com.aishop.api.dto.PayOrderNotifyDTO;
import com.aishop.api.enums.OrderStatusEnum;
import com.aishop.api.support.CouponFeignSupport;
import com.aishop.api.support.PayFeignSupport;
import com.aishop.api.support.StockFeignSupport;
import com.aishop.component.PayOrderRedisComponent;
import com.aishop.component.RemoteCompensateRecorder;
import com.aishop.entity.po.OrderCouponRel;
import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.po.OrderItem;
import com.aishop.entity.po.ProductItem;
import com.aishop.entity.query.OrderCouponRelQuery;
import com.aishop.entity.query.OrderInfoQuery;
import com.aishop.entity.query.OrderItemQuery;
import com.aishop.integration.CommerceOutcomeClient;
import com.aishop.mappers.OrderCouponRelMapper;
import com.aishop.mappers.OrderInfoMapper;
import com.aishop.mappers.OrderItemMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OrderAggregateCancellationTest {

    @Mock
    private OrderInfoMapper<OrderInfo, OrderInfoQuery> orderInfoMapper;
    @Mock
    private OrderItemMapper<OrderItem, OrderItemQuery> orderItemMapper;
    @Mock
    private OrderCouponRelMapper<OrderCouponRel, OrderCouponRelQuery> orderCouponRelMapper;
    @Mock
    private PayOrderRedisComponent payOrderRedisComponent;
    @Mock
    private StockFeignSupport stockFeignSupport;
    @Mock
    private PayFeignSupport payFeignSupport;
    @Mock
    private CouponFeignSupport couponFeignSupport;
    @Mock
    private RemoteCompensateRecorder remoteCompensateRecorder;
    @Mock
    private CommerceOutcomeClient commerceOutcomeClient;
    @InjectMocks
    private OrderInfoServiceImpl service;

    @BeforeEach
    void executeLifecycleLockBody() {
        lenient().doAnswer(invocation -> {
            invocation.getArgument(1, Runnable.class).run();
            return null;
        }).when(payOrderRedisComponent).runWithPayOrderLifecycleLock(anyString(), any(Runnable.class));
    }

    @Test
    void cancellingOneChildCancelsAndRestoresTheWholePaymentAggregate() {
        OrderInfo first = order("order-1", "pay-1", OrderStatusEnum.WAIT_PAYMENT);
        OrderInfo second = order("order-2", "pay-1", OrderStatusEnum.WAIT_PAYMENT);
        when(orderInfoMapper.selectByOrderId("order-1")).thenReturn(first);
        when(orderInfoMapper.selectList(any(OrderInfoQuery.class))).thenReturn(List.of(first, second));
        when(orderInfoMapper.updateByParam(any(OrderInfo.class), any(OrderInfoQuery.class))).thenReturn(2);
        when(orderItemMapper.selectList(any(OrderItemQuery.class))).thenAnswer(invocation -> {
            String orderId = invocation.getArgument(0, OrderItemQuery.class).getOrderId();
            return List.of(item(orderId, orderId.equals("order-1") ? "p1" : "p2"));
        });
        when(orderCouponRelMapper.selectList(any(OrderCouponRelQuery.class))).thenReturn(List.of());

        service.cancelOrder("user-1", "order-1", OrderStatusEnum.WAIT_PAYMENT);

        ArgumentCaptor<OrderInfo> updateCaptor = ArgumentCaptor.forClass(OrderInfo.class);
        ArgumentCaptor<OrderInfoQuery> queryCaptor = ArgumentCaptor.forClass(OrderInfoQuery.class);
        verify(orderInfoMapper).updateByParam(updateCaptor.capture(), queryCaptor.capture());
        assertEquals(OrderStatusEnum.CANCELLED.getStatus(), updateCaptor.getValue().getOrderStatus());
        assertEquals("pay-1", queryCaptor.getValue().getPayOrderId());
        assertEquals(OrderStatusEnum.WAIT_PAYMENT.getStatus(), queryCaptor.getValue().getOrderStatus());

        @SuppressWarnings("unchecked")
        ArgumentCaptor<List<ProductItem>> itemsCaptor = ArgumentCaptor.forClass(List.class);
        verify(stockFeignSupport).restoreOrderStock(org.mockito.ArgumentMatchers.eq("pay-1"), itemsCaptor.capture());
        assertEquals(2, itemsCaptor.getValue().size());
        verify(payFeignSupport).markClosed("pay-1");
    }

    @Test
    void repeatedCancellationOfClosedAggregateIsANoOp() {
        OrderInfo first = order("order-1", "pay-1", OrderStatusEnum.CANCELLED);
        OrderInfo second = order("order-2", "pay-1", OrderStatusEnum.CANCELLED);
        when(orderInfoMapper.selectByOrderId("order-1")).thenReturn(first);
        when(orderInfoMapper.selectList(any(OrderInfoQuery.class))).thenReturn(List.of(first, second));

        service.cancelOrder("user-1", "order-1", OrderStatusEnum.WAIT_PAYMENT);

        verify(orderInfoMapper, never()).updateByParam(any(), any());
        verify(stockFeignSupport, never()).restoreOrderStock(anyString(), any());
        verify(payFeignSupport, never()).markClosed(anyString());
    }

    @Test
    void paidSiblingPreventsAggregateCancellation() {
        OrderInfo first = order("order-1", "pay-1", OrderStatusEnum.WAIT_PAYMENT);
        OrderInfo second = order("order-2", "pay-1", OrderStatusEnum.PAID);
        when(orderInfoMapper.selectByOrderId("order-1")).thenReturn(first);
        when(orderInfoMapper.selectList(any(OrderInfoQuery.class))).thenReturn(List.of(first, second));

        assertThrows(RuntimeException.class,
                () -> service.cancelOrder("user-1", "order-1", OrderStatusEnum.WAIT_PAYMENT));

        verify(orderInfoMapper, never()).updateByParam(any(), any());
        verify(stockFeignSupport, never()).restoreOrderStock(anyString(), any());
    }

    @Test
    void staleRedisCloseMarkerCannotOverrideWaitingDatabaseState() {
        OrderInfo waiting = order("order-1", "pay-1", OrderStatusEnum.WAIT_PAYMENT);
        when(orderInfoMapper.selectList(any(OrderInfoQuery.class))).thenReturn(List.of(waiting));
        PayOrderNotifyDTO notification = new PayOrderNotifyDTO("pay-1", "channel-1");

        assertThrows(RuntimeException.class, () -> ReflectionTestUtils.invokeMethod(
                service, "handlePaySuccessConflict", "pay-1", notification));

        verify(payOrderRedisComponent, never()).isPayOrderCloseMarked(anyString());
        verify(payFeignSupport, never()).markRefunded(anyString());
    }

    private static OrderInfo order(String orderId, String payOrderId, OrderStatusEnum status) {
        OrderInfo order = new OrderInfo();
        order.setOrderId(orderId);
        order.setPayOrderId(payOrderId);
        order.setUserId("user-1");
        order.setOrderStatus(status.getStatus());
        return order;
    }

    private static OrderItem item(String orderId, String productId) {
        OrderItem item = new OrderItem();
        item.setOrderId(orderId);
        item.setOrderItemId(orderId + "-item");
        item.setProductId(productId);
        item.setPropertyValueIdHash(productId + "-sku");
        item.setBuyCount(1);
        return item;
    }
}
