package com.aishop.component;

import com.aishop.api.dto.NotificationMessageDTO;
import com.aishop.biz.UserNotificationService;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.entity.po.UserNotification;
import com.aishop.redis.RedisUtils;
import com.rabbitmq.client.Channel;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class RabbitMQNotifyListenerComponentTest {

    @Mock
    private UserNotificationService userNotificationService;
    @Mock
    private RedisUtils redisUtils;
    @Mock
    private NotifyPushPublisher notifyPushPublisher;
    @Mock
    private MqListenerHelper mqListenerHelper;
    @Mock
    private Channel channel;
    @InjectMocks
    private RabbitMQNotifyListenerComponent listener;

    @Test
    void redeliveryUsesStablePrimaryKeyAndOnlyPushesNewInsert() throws Exception {
        NotificationMessageDTO payload =
                new NotificationMessageDTO("u1", "订单更新", "已发货", "order", "o1");
        Message first = message(17L);
        Message redelivery = message(18L);
        when(mqListenerHelper.tryBeginConsume(any(), eq(MqListenerHelper.CONSUME_IDEMPOTENCY_TTL_HIGH_SECONDS)))
                .thenReturn(true);
        when(mqListenerHelper.resolveIdempotencyKey(any())).thenReturn("notify:u1:order:o1");
        when(userNotificationService.insertIfAbsent(any())).thenReturn(true, false);

        listener.handleNotify(payload, channel, first);
        listener.handleNotify(payload, channel, redelivery);

        ArgumentCaptor<UserNotification> notifications =
                ArgumentCaptor.forClass(UserNotification.class);
        verify(userNotificationService, times(2)).insertIfAbsent(notifications.capture());
        assertEquals(
                notifications.getAllValues().get(0).getNotificationId(),
                notifications.getAllValues().get(1).getNotificationId());
        verify(notifyPushPublisher).push(notifications.getAllValues().get(0));
        verify(mqListenerHelper).clearConsumeRetry(RabbitMQConfig.NOTIFY_QUEUE, first);
        verify(mqListenerHelper).clearConsumeRetry(RabbitMQConfig.NOTIFY_QUEUE, redelivery);
        verify(channel).basicAck(17L, false);
        verify(channel).basicAck(18L, false);
    }

    @Test
    void persistenceFailureUsesDurableDelayedRetry() throws Exception {
        NotificationMessageDTO payload =
                new NotificationMessageDTO("u1", "订单更新", "已发货", "order", "o1");
        Message message = message(19L);
        when(mqListenerHelper.tryBeginConsume(
                message, MqListenerHelper.CONSUME_IDEMPOTENCY_TTL_HIGH_SECONDS)).thenReturn(true);
        when(mqListenerHelper.resolveIdempotencyKey(message)).thenReturn("notify:u1:order:o1");
        IllegalStateException failure = new IllegalStateException("database unavailable");
        doThrow(failure).when(userNotificationService).insertIfAbsent(any());

        listener.handleNotify(payload, channel, message);

        verify(mqListenerHelper).nackWithRetryOrDlq(
                channel,
                19L,
                message,
                RabbitMQConfig.NOTIFY_QUEUE,
                payload,
                failure);
    }

    private static Message message(long deliveryTag) {
        MessageProperties properties = new MessageProperties();
        properties.setDeliveryTag(deliveryTag);
        return new Message(new byte[0], properties);
    }
}
