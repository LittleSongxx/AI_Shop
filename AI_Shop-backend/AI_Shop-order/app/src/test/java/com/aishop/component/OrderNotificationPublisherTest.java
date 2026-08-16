package com.aishop.component;

import com.aishop.api.dto.NotificationMessageDTO;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.TransactionalMqSender;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;

@ExtendWith(MockitoExtension.class)
class OrderNotificationPublisherTest {

    @Mock
    private TransactionalMqSender transactionalMqSender;
    @InjectMocks
    private OrderNotificationPublisher publisher;

    @Test
    void notificationUsesOrderOutboxAndStableBusinessKey() {
        publisher.send("u1", "订单已发货", "物流单号 123", "logistics", "o1");

        ArgumentCaptor<NotificationMessageDTO> payload =
                ArgumentCaptor.forClass(NotificationMessageDTO.class);
        verify(transactionalMqSender).sendAfterCommit(
                eq(RabbitMQConfig.NOTIFY_EXCHANGE),
                eq(RabbitMQConfig.NOTIFY_KEY),
                payload.capture(),
                eq("notify:u1:logistics:o1"),
                eq(MessageReliabilityLevelEnum.HIGH));
        assertEquals("u1", payload.getValue().getUserId());
        assertEquals("订单已发货", payload.getValue().getTitle());
    }
}
