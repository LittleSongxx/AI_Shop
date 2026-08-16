package com.aishop.component;

import com.rabbitmq.client.Channel;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionSynchronizationUtils;

import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

@ExtendWith(MockitoExtension.class)
class RabbitMQPayOrderDeadListenerComponentTest {

    @Mock
    private MqListenerHelper mqListenerHelper;
    @Mock
    private Channel channel;
    @InjectMocks
    private RabbitMQPayOrderDeadListenerComponent listener;

    @AfterEach
    void clearTransactionSynchronization() {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.clearSynchronization();
        }
    }

    @Test
    void rollbackReleasesLeaseWithoutSettlingMessageTwice() throws Exception {
        Message message = message(21L, "pay.confirm.dead.queue");
        TransactionSynchronizationManager.initSynchronization();
        ReflectionTestUtils.invokeMethod(
                listener, "registerAckSync", 21L, channel, message);

        TransactionSynchronizationUtils.invokeAfterCompletion(
                TransactionSynchronizationManager.getSynchronizations(),
                TransactionSynchronization.STATUS_ROLLED_BACK);

        verify(mqListenerHelper).releaseConsume(message);
        verify(channel, never()).basicAck(21L, false);
        verify(channel, never()).basicNack(21L, false, false);
    }

    @Test
    void committedBusinessTransactionIsAckedEvenWhenRedisCompletionMarkerFails()
            throws Exception {
        Message message = message(22L, "pay.logistics.dead.queue");
        doThrow(new IllegalStateException("redis unavailable"))
                .when(mqListenerHelper)
                .clearConsumeRetry("pay.logistics.dead.queue", message);
        TransactionSynchronizationManager.initSynchronization();
        ReflectionTestUtils.invokeMethod(
                listener, "registerAckSync", 22L, channel, message);

        TransactionSynchronizationUtils.invokeAfterCommit(
                TransactionSynchronizationManager.getSynchronizations());

        verify(channel).basicAck(22L, false);
    }

    private static Message message(long deliveryTag, String queue) {
        MessageProperties properties = new MessageProperties();
        properties.setDeliveryTag(deliveryTag);
        properties.setConsumerQueue(queue);
        return new Message(new byte[0], properties);
    }
}
