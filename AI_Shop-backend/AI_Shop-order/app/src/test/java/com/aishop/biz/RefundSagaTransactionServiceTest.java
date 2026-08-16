package com.aishop.biz;

import com.aishop.component.OrderNotificationPublisher;
import com.aishop.constants.TransactionalMqSender;
import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.po.OrderItem;
import com.aishop.entity.po.RefundRequest;
import com.aishop.integration.CommerceOutcomeClient;
import com.aishop.mappers.OrderInfoMapper;
import com.aishop.mappers.OrderItemMapper;
import com.aishop.mappers.RefundRequestMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class RefundSagaTransactionServiceTest {

    @Mock
    private RefundRequestMapper refundRequestMapper;
    @Mock
    private OrderInfoMapper<OrderInfo, ?> orderInfoMapper;
    @Mock
    private OrderItemMapper<OrderItem, ?> orderItemMapper;
    @Mock
    private TransactionalMqSender transactionalMqSender;
    @Mock
    private CommerceOutcomeClient commerceOutcomeClient;
    @Mock
    private OrderNotificationPublisher orderNotificationPublisher;
    @InjectMocks
    private RefundSagaTransactionService service;

    @Test
    void completionNotificationUsesDurableOrderOutbox() {
        RefundRequest request = new RefundRequest();
        request.setRefundRequestId("r1");
        request.setOrderId("o1");
        request.setOrderItemId("i1");
        request.setUserId("u1");
        request.setStatus("STOCK_PENDING");
        when(refundRequestMapper.selectById("r1")).thenReturn(request);
        when(orderItemMapper.selectByOrderItemId("i1")).thenReturn(null);

        service.markCompleted("r1");

        verify(refundRequestMapper).markCompleted("r1");
        verify(orderNotificationPublisher).send(
                "u1",
                "退款已完成",
                "订单 o1 的退款已完成，款项将按支付渠道到账。",
                "refund_complete",
                "r1");
    }
}
