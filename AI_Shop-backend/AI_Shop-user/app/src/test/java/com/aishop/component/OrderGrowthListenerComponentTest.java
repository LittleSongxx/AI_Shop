package com.aishop.component;

import com.aishop.api.dto.OrderGrowthEventDTO;
import com.aishop.biz.UserMemberProfileService;
import com.aishop.constants.RabbitMQConfig;
import com.rabbitmq.client.Channel;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;

import java.math.BigDecimal;

import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OrderGrowthListenerComponentTest {

    @Mock
    private UserMemberProfileService userMemberProfileService;
    @Mock
    private MqListenerHelper mqListenerHelper;
    @Mock
    private Channel channel;
    @InjectMocks
    private OrderGrowthListenerComponent listener;

    @Test
    void committedOrDuplicateEventIsAcknowledgedWithoutRedisClaim() throws Exception {
        OrderGrowthEventDTO event = event();
        Message message = message(17L);
        when(userMemberProfileService.applyOrderGrowth(event)).thenReturn(false);

        listener.handle(event, channel, message);

        verify(channel).basicAck(17L, false);
        verify(mqListenerHelper).clearConsumeRetry(RabbitMQConfig.USER_GROWTH_QUEUE, message);
        verify(mqListenerHelper, never()).tryBeginConsume(eq(message), anyLong());
    }

    @Test
    void failedDatabaseTransactionIsNackedForRetryOrDeadLetter() throws Exception {
        OrderGrowthEventDTO event = event();
        Message message = message(18L);
        RuntimeException failure = new RuntimeException("db unavailable");
        when(userMemberProfileService.applyOrderGrowth(event)).thenThrow(failure);

        listener.handle(event, channel, message);

        verify(channel, never()).basicAck(18L, false);
        verify(mqListenerHelper).nackWithRetryOrDlq(
                channel,
                18L,
                message,
                RabbitMQConfig.USER_GROWTH_QUEUE,
                event,
                failure);
    }

    private static OrderGrowthEventDTO event() {
        return new OrderGrowthEventDTO(
                "order-1", "user-1", new BigDecimal("268.00"));
    }

    private static Message message(long deliveryTag) {
        MessageProperties properties = new MessageProperties();
        properties.setDeliveryTag(deliveryTag);
        return new Message(new byte[0], properties);
    }
}
