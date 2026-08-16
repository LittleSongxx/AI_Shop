package com.aishop.component;

import com.aishop.constants.RabbitMQConfig;
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
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class MqListenerHelperTest {

    @Mock
    private MqConsumerIdempotencyHelper idempotencyHelper;
    @Mock
    private MqConsumeFailureRecorder failureRecorder;
    @Mock
    private MqPublisherConfirmHelper publisherConfirmHelper;
    @Mock
    private Channel channel;
    @InjectMocks
    private MqListenerHelper helper;

    @Test
    void failedConsumePublishesConfirmedDelayedRetryBeforeAck() throws Exception {
        Message source = message(17L, 0);
        when(idempotencyHelper.resolveIdempotencyKey(source)).thenReturn("message-1");

        helper.nackWithRetryOrDlq(
                channel, 17L, source, RabbitMQConfig.USER_GROWTH_QUEUE,
                "payload", new IllegalStateException("temporary"));

        ArgumentCaptor<Message> retry = ArgumentCaptor.forClass(Message.class);
        verify(publisherConfirmHelper).sendRawAndAwaitConfirm(
                eq(RabbitMQConfig.MQ_RETRY_EXCHANGE),
                eq(RabbitMQConfig.retryRoutingKey(RabbitMQConfig.USER_GROWTH_QUEUE, 1)),
                retry.capture(),
                anyString());
        assertEquals(1, ((Number) retry.getValue().getMessageProperties()
                .getHeader(MqListenerHelper.HEADER_RETRY_ATTEMPT)).intValue());
        assertNull(retry.getValue().getMessageProperties()
                .getHeader(MqConsumerIdempotencyHelper.HEADER_CONSUMER_LEASE_TOKEN));
        verify(channel).basicAck(17L, false);
        verify(channel, never()).basicNack(17L, false, false);
    }

    @Test
    void retryExhaustionPublishesToFinalFailureQueue() throws Exception {
        Message source = message(18L, 3);
        when(idempotencyHelper.resolveIdempotencyKey(source)).thenReturn("message-2");
        RuntimeException failure = new RuntimeException("permanent");

        helper.nackWithRetryOrDlq(
                channel, 18L, source, RabbitMQConfig.REFUND_RESULT_QUEUE,
                "payload", failure);

        verify(failureRecorder).record(
                RabbitMQConfig.REFUND_RESULT_QUEUE, source, "payload", failure);
        verify(publisherConfirmHelper).sendRawAndAwaitConfirm(
                eq(RabbitMQConfig.MQ_FAILURE_EXCHANGE),
                eq(RabbitMQConfig.MQ_FAILURE_KEY),
                any(Message.class),
                anyString());
        verify(channel).basicAck(18L, false);
    }

    @Test
    void missingIdempotencyKeyStillReachesFinalFailureQueue() throws Exception {
        Message source = message(19L, 0);
        when(idempotencyHelper.resolveIdempotencyKey(source)).thenReturn(null);
        RuntimeException failure = new RuntimeException("invalid message");

        helper.nackWithRetryOrDlq(
                channel, 19L, source, RabbitMQConfig.NOTIFY_QUEUE,
                "payload", failure);

        verify(failureRecorder).record(RabbitMQConfig.NOTIFY_QUEUE, source, "payload", failure);
        verify(publisherConfirmHelper).sendRawAndAwaitConfirm(
                eq(RabbitMQConfig.MQ_FAILURE_EXCHANGE),
                eq(RabbitMQConfig.MQ_FAILURE_KEY),
                any(Message.class),
                anyString());
        verify(channel).basicAck(19L, false);
    }

    @Test
    void activeConsumerLeaseDefersDuplicateWithoutStealingIt() throws Exception {
        Message source = message(20L, 1);
        when(idempotencyHelper.resolveClaimResult(source))
                .thenReturn(MqConsumerIdempotencyHelper.ClaimResult.BUSY);
        when(idempotencyHelper.resolveIdempotencyKey(source)).thenReturn("message-3");

        helper.ackCompletedOrDeferBusy(
                channel, 20L, source, RabbitMQConfig.USER_GROWTH_QUEUE);

        ArgumentCaptor<Message> deferred = ArgumentCaptor.forClass(Message.class);
        verify(publisherConfirmHelper).sendRawAndAwaitConfirm(
                eq(RabbitMQConfig.MQ_RETRY_EXCHANGE),
                eq(RabbitMQConfig.retryRoutingKey(RabbitMQConfig.USER_GROWTH_QUEUE, 2)),
                deferred.capture(),
                anyString());
        assertEquals(1, ((Number) deferred.getValue().getMessageProperties()
                .getHeader(MqListenerHelper.HEADER_RETRY_ATTEMPT)).intValue());
        assertNull(deferred.getValue().getMessageProperties()
                .getHeader(MqConsumerIdempotencyHelper.HEADER_CONSUMER_LEASE_TOKEN));
        assertNull(deferred.getValue().getMessageProperties()
                .getHeader(MqConsumerIdempotencyHelper.HEADER_CONSUMER_CLAIM_RESULT));
        verify(channel).basicAck(20L, false);
    }

    @Test
    void completedDuplicateIsAcknowledgedWithoutRepublish() throws Exception {
        Message source = message(21L, 0);
        when(idempotencyHelper.resolveClaimResult(source))
                .thenReturn(MqConsumerIdempotencyHelper.ClaimResult.COMPLETED);

        helper.ackCompletedOrDeferBusy(
                channel, 21L, source, RabbitMQConfig.NOTIFY_QUEUE);

        verify(channel).basicAck(21L, false);
        verify(publisherConfirmHelper, never()).sendRawAndAwaitConfirm(
                anyString(), anyString(), any(Message.class), anyString());
    }

    private static Message message(long deliveryTag, int retryAttempt) {
        MessageProperties properties = new MessageProperties();
        properties.setDeliveryTag(deliveryTag);
        properties.setHeader(MqListenerHelper.HEADER_RETRY_ATTEMPT, retryAttempt);
        properties.setHeader(
                MqConsumerIdempotencyHelper.HEADER_CONSUMER_LEASE_TOKEN, "stale-token");
        return new Message("payload".getBytes(), properties);
    }
}
