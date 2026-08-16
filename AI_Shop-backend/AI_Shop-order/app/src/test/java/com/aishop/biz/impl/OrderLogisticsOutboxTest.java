package com.aishop.biz.impl;

import com.aishop.api.dto.PayOrderMessageDTO;
import com.aishop.biz.OrderInfoService;
import com.aishop.biz.OrderLogisticsInfoRecordService;
import com.aishop.component.OrderNotificationPublisher;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.TransactionalMqSender;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.po.OrderLogisticsInfo;
import com.aishop.entity.po.OrderLogisticsInfoRecord;
import com.aishop.entity.query.OrderInfoQuery;
import com.aishop.entity.query.OrderLogisticsInfoQuery;
import com.aishop.mappers.OrderLogisticsInfoMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OrderLogisticsOutboxTest {

    @Mock
    private OrderLogisticsInfoMapper<OrderLogisticsInfo, OrderLogisticsInfoQuery> mapper;
    @Mock
    private OrderLogisticsInfoRecordService recordService;
    @Mock
    private OrderInfoService orderInfoService;
    @Mock
    private TransactionalMqSender transactionalMqSender;
    @Mock
    private OrderNotificationPublisher orderNotificationPublisher;
    @InjectMocks
    private OrderLogisticsInfoServiceImpl service;

    @Test
    void deliveryRegistersConfirmAndNotificationMessagesInOutbox() {
        OrderLogisticsInfo logistics = new OrderLogisticsInfo();
        logistics.setOrderId("o1");
        logistics.setLogisticsNo("L123");
        logistics.setSenderAddress("上海");
        when(mapper.updateByParam(any(), any())).thenReturn(1);
        when(orderInfoService.updateByParam(any(OrderInfo.class), any(OrderInfoQuery.class)))
                .thenReturn(1);
        OrderInfo shipped = new OrderInfo();
        shipped.setUserId("u1");
        when(orderInfoService.getOrderInfoByOrderId("o1")).thenReturn(shipped);

        service.delivery(logistics);

        ArgumentCaptor<PayOrderMessageDTO> confirm =
                ArgumentCaptor.forClass(PayOrderMessageDTO.class);
        verify(transactionalMqSender).sendAfterCommit(
                eq(RabbitMQConfig.PAY_EXCHANGE),
                eq(RabbitMQConfig.PAY_CONFIRM_DELAY_KEY),
                confirm.capture(),
                eq("pay:confirm:o1"),
                eq(MessageReliabilityLevelEnum.STANDARD));
        assertEquals("o1", confirm.getValue().getOrderId());
        verify(recordService).add(any(OrderLogisticsInfoRecord.class));
        verify(orderNotificationPublisher).send(
                "u1",
                "订单已发货",
                "您的订单 o1 已发货，物流单号：L123",
                "logistics",
                "o1");
    }
}
