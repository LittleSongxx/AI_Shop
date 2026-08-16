package com.aishop.component;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.amqp.AmqpException;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageDeliveryMode;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.rabbit.connection.CachingConnectionFactory;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Collections;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

@EnabledIfEnvironmentVariable(named = "RUN_RABBIT_INTEGRATION", matches = "1")
class MqPublisherConfirmRabbitIntegrationTest {

    @Test
    @Timeout(value = 20, unit = TimeUnit.SECONDS)
    void confirmedRouteSucceedsAndMandatoryReturnFails() {
        String suffix = UUID.randomUUID().toString().replace("-", "");
        String exchange = "codex.confirm." + suffix;
        String queue = "codex.confirm." + suffix;
        CachingConnectionFactory connectionFactory = connectionFactory();
        RabbitTemplate template = new RabbitTemplate(connectionFactory);
        MqPublisherConfirmHelper helper = new MqPublisherConfirmHelper();
        ReflectionTestUtils.setField(helper, "rabbitTemplate", template);
        ReflectionTestUtils.setField(helper, "defaultConfirmTimeoutMs", 5_000L);
        ReflectionTestUtils.setField(helper, "confirmTimeoutByExchange", Collections.emptyMap());
        helper.attachCallbacks();

        try {
            template.execute(channel -> {
                channel.exchangeDeclare(exchange, "direct", false, true, null);
                channel.queueDeclare(queue, false, true, true, null);
                channel.queueBind(queue, exchange, "routed");
                return null;
            });

            assertDoesNotThrow(() -> helper.sendRawAndAwaitConfirm(
                    exchange, "routed", message("routed"), "confirm-routed-" + suffix));
            assertThrows(AmqpException.class, () -> helper.sendRawAndAwaitConfirm(
                    exchange, "missing", message("unroutable"), "confirm-return-" + suffix));
        } finally {
            try {
                template.execute(channel -> {
                    channel.queueDelete(queue);
                    channel.exchangeDelete(exchange);
                    return null;
                });
            } finally {
                connectionFactory.destroy();
            }
        }
    }

    private static CachingConnectionFactory connectionFactory() {
        String rabbitHost = System.getenv().getOrDefault("RABBIT_HOST", "127.0.0.1");
        int rabbitPort = Integer.parseInt(System.getenv().getOrDefault("RABBIT_PORT", "5673"));
        CachingConnectionFactory connectionFactory =
                new CachingConnectionFactory(rabbitHost, rabbitPort);
        connectionFactory.setUsername(System.getenv().getOrDefault("RABBIT_USER", "aishop"));
        connectionFactory.setPassword(System.getenv().getOrDefault("RABBIT_PASSWORD", "aishop"));
        connectionFactory.setPublisherConfirmType(CachingConnectionFactory.ConfirmType.CORRELATED);
        connectionFactory.setPublisherReturns(true);
        return connectionFactory;
    }

    private static Message message(String body) {
        MessageProperties properties = new MessageProperties();
        properties.setMessageId(UUID.randomUUID().toString());
        properties.setDeliveryMode(MessageDeliveryMode.PERSISTENT);
        return new Message(body.getBytes(), properties);
    }
}
