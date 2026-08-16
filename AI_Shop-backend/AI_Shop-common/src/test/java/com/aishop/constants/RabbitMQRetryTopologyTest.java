package com.aishop.constants;

import org.junit.jupiter.api.Test;
import org.springframework.amqp.core.Declarable;
import org.springframework.amqp.core.Declarables;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.rabbit.config.SimpleRabbitListenerContainerFactory;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.boot.autoconfigure.amqp.SimpleRabbitListenerContainerFactoryConfigurer;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Set;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class RabbitMQRetryTopologyTest {

    @Test
    void everyBusinessConsumerWithDelayedRetryHasItsQueuesDeclared() {
        RabbitMQConfig config = new RabbitMQConfig();
        ReflectionTestUtils.setField(config, "queueType", "quorum");

        Declarables declarables = config.mqRetryTopology();
        Set<String> queueNames = declarables.getDeclarables().stream()
                .filter(Queue.class::isInstance)
                .map(Queue.class::cast)
                .map(Queue::getName)
                .collect(Collectors.toSet());

        assertRetryQueues(queueNames, RabbitMQConfig.RUSHING_ORDER_QUEUE);
        assertRetryQueues(queueNames, RabbitMQConfig.REFUND_STOCK_QUEUE);
        assertRetryQueues(queueNames, RabbitMQConfig.REFUND_RESULT_QUEUE);
        assertRetryQueues(queueNames, RabbitMQConfig.USER_GROWTH_QUEUE);
        assertRetryQueues(queueNames, RabbitMQConfig.USER_TEMP_BAN_DEAD_QUEUE);
    }

    @Test
    void listenerFactoryAppliesSpringBootRabbitProperties() {
        RabbitMQConfig config = new RabbitMQConfig();
        ConnectionFactory connectionFactory = mock(ConnectionFactory.class);
        SimpleRabbitListenerContainerFactoryConfigurer configurer =
                mock(SimpleRabbitListenerContainerFactoryConfigurer.class);

        SimpleRabbitListenerContainerFactory factory =
                config.rabbitListenerContainerFactory(connectionFactory, configurer);

        verify(configurer).configure(factory, connectionFactory);
    }

    @Test
    void durableNotificationQueueDoesNotSilentlyExpireMessages() {
        RabbitMQConfig config = new RabbitMQConfig();
        ReflectionTestUtils.setField(config, "queueType", "quorum");

        Queue queue = config.notifyQueue();

        assertTrue(queue.isDurable());
        assertFalse(queue.getArguments().containsKey("x-message-ttl"));
    }

    private static void assertRetryQueues(Set<String> queueNames, String queueName) {
        for (int attempt = 1; attempt <= 3; attempt++) {
            assertTrue(
                    queueNames.contains(RabbitMQConfig.retryRoutingKey(queueName, attempt)),
                    "missing retry queue for " + queueName + " attempt " + attempt);
        }
    }
}
