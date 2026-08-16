package com.aishop.task;

import com.aishop.component.OrderNotificationPublisher;
import com.aishop.entity.po.OrderInfo;
import com.aishop.mappers.OrderInfoMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OrderLifecycleNotificationTaskTest {

    private final OrderInfoMapper<OrderInfo, ?> mapper = mock(OrderInfoMapper.class);
    private final OrderNotificationPublisher orderNotificationPublisher =
            mock(OrderNotificationPublisher.class);
    private final OrderLifecycleNotificationTask task = new OrderLifecycleNotificationTask();

    @BeforeEach
    void setUp() {
        ReflectionTestUtils.setField(task, "orderInfoMapper", mapper);
        ReflectionTestUtils.setField(task, "orderNotificationPublisher", orderNotificationPublisher);
        ReflectionTestUtils.setField(task, "delayEnabled", true);
        ReflectionTestUtils.setField(task, "delayHours", 24);
        ReflectionTestUtils.setField(task, "batchSize", 100);
    }

    @Test
    void delayedPaidOrderUsesStableBusinessDeduplicationKey() {
        OrderInfo order = new OrderInfo();
        order.setOrderId("order-1");
        order.setUserId("user-1");
        when(mapper.selectDelayedPaidOrders(24, 100)).thenReturn(List.of(order));

        task.notifyDelayedOrders();

        verify(orderNotificationPublisher).send(
                "user-1",
                "订单发货延迟提醒",
                "订单 order-1 尚未发货，我们已记录延迟状态，请留意后续物流更新。",
                "order_delay",
                "order-1");
    }

    @Test
    void disabledTaskDoesNotQueryOrNotify() {
        ReflectionTestUtils.setField(task, "delayEnabled", false);

        task.notifyDelayedOrders();

        verify(mapper, never()).selectDelayedPaidOrders(24, 100);
        verify(orderNotificationPublisher, never()).send(
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.any());
    }

    @Test
    void delayedCandidateMapperExposesConcreteOrderInfoElements() throws Exception {
        var returnType = (java.lang.reflect.ParameterizedType) OrderInfoMapper.class
                .getMethod("selectDelayedPaidOrders", int.class, int.class)
                .getGenericReturnType();

        assertEquals(OrderInfo.class, returnType.getActualTypeArguments()[0]);
    }
}
