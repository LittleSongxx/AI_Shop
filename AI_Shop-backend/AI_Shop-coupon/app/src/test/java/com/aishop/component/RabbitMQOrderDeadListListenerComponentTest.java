package com.aishop.component;

import com.aishop.api.dto.RushingCouponMessageDTO;
import com.aishop.api.support.OrderFeignSupport;
import com.aishop.biz.DiscountCouponService;
import com.aishop.biz.UserCouponService;
import com.aishop.constants.RabbitMQConfig;
import com.rabbitmq.client.Channel;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InOrder;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.transaction.annotation.Transactional;

import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class RabbitMQOrderDeadListListenerComponentTest {

    @Mock
    private DiscountCouponService discountCouponService;
    @Mock
    private UserCouponService userCouponService;
    @Mock
    private OrderFeignSupport orderFeignSupport;
    @Mock
    private MqListenerHelper mqListenerHelper;
    @Mock
    private Channel channel;
    @InjectMocks
    private RabbitMQOrderDeadListListenerComponent listener;

    @Test
    void preOrderTimeoutMarksConsumptionCompletedBeforeAck() throws Exception {
        RushingCouponMessageDTO payload = payload(null);
        Message message = message(17L);
        when(mqListenerHelper.tryBeginConsume(message, 86_400L)).thenReturn(true);
        when(userCouponService.getUserCouponByUserCouponId("uc-1")).thenReturn(null);

        listener.handleDeadOrder(payload, channel, message);

        verify(discountCouponService).releaseRushCouponReserve("coupon-1", "user-1");
        InOrder completion = inOrder(mqListenerHelper, channel);
        completion.verify(mqListenerHelper)
                .clearConsumeRetry(RabbitMQConfig.RUSHING_DEAD_QUEUE, message);
        completion.verify(channel).basicAck(17L, false);
    }

    @Test
    void missingOrderRollbackMarksConsumptionCompletedBeforeAck() throws Exception {
        RushingCouponMessageDTO payload = payload("order-1");
        Message message = message(18L);
        when(mqListenerHelper.tryBeginConsume(message, 86_400L)).thenReturn(true);
        when(userCouponService.getUserCouponByUserCouponId("uc-1")).thenReturn(null);
        when(orderFeignSupport.getOrder("order-1")).thenReturn(null);

        listener.handleDeadOrder(payload, channel, message);

        verify(discountCouponService).releaseRushRedisReserve("coupon-1", "user-1");
        InOrder completion = inOrder(mqListenerHelper, channel);
        completion.verify(mqListenerHelper)
                .clearConsumeRetry(RabbitMQConfig.RUSHING_DEAD_QUEUE, message);
        completion.verify(channel).basicAck(18L, false);
    }

    @Test
    void listenerDoesNotWrapRemoteCallsAndAckInOneDatabaseTransaction() throws Exception {
        assertNull(RabbitMQOrderDeadListListenerComponent.class
                .getMethod(
                        "handleDeadOrder",
                        RushingCouponMessageDTO.class,
                        Channel.class,
                        Message.class)
                .getAnnotation(Transactional.class));
    }

    private static RushingCouponMessageDTO payload(String orderId) {
        RushingCouponMessageDTO payload = new RushingCouponMessageDTO();
        payload.setUserId("user-1");
        payload.setCouponId("coupon-1");
        payload.setUserCouponId("uc-1");
        payload.setOrderId(orderId);
        return payload;
    }

    private static Message message(long deliveryTag) {
        MessageProperties properties = new MessageProperties();
        properties.setDeliveryTag(deliveryTag);
        properties.setConsumerQueue(RabbitMQConfig.RUSHING_DEAD_QUEUE);
        return new Message(new byte[0], properties);
    }
}
