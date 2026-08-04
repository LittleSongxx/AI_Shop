package com.aishop.component;

import com.aishop.api.dto.OrderGrowthEventDTO;
import com.aishop.utils.JsonUtils;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.rabbit.connection.CachingConnectionFactory;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;

import java.math.BigDecimal;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNotNull;

@EnabledIfEnvironmentVariable(named = "RUN_RABBIT_INTEGRATION", matches = "1")
class OrderGrowthRabbitIntegrationTest {

    @Test
    @Timeout(value = 15, unit = TimeUnit.SECONDS)
    void outboxMapRoundTripsThroughRabbitAndConvertsToListenerDto() {
        String suffix = UUID.randomUUID().toString().replace("-", "");
        String exchange = "codex.user.growth." + suffix;
        String queue = "codex.user.growth." + suffix;
        String routingKey = "growth";
        CachingConnectionFactory connectionFactory =
                new CachingConnectionFactory("127.0.0.1", 15673);
        connectionFactory.setUsername("aishop");
        connectionFactory.setPassword("aishop");
        RabbitTemplate template = new RabbitTemplate(connectionFactory);
        template.setMessageConverter(new Jackson2JsonMessageConverter(JsonUtils.mapper()));

        try {
            template.execute(channel -> {
                channel.exchangeDeclare(exchange, "direct", false, true, null);
                channel.queueDeclare(queue, false, true, true, null);
                channel.queueBind(queue, exchange, routingKey);
                return null;
            });

            Map<String, Object> outboxPayload = JsonUtils.parseObject(
                    JsonUtils.toJson(new OrderGrowthEventDTO(
                            "order-rabbit", "user-rabbit", new BigDecimal("268.00"))),
                    Map.class);
            template.convertAndSend(exchange, routingKey, outboxPayload);

            Message message = template.receive(queue, 5_000);
            assertNotNull(message);
            message.getMessageProperties().setInferredArgumentType(OrderGrowthEventDTO.class);
            Object converted = template.getMessageConverter().fromMessage(message);
            OrderGrowthEventDTO event = assertInstanceOf(OrderGrowthEventDTO.class, converted);
            assertEquals("order-rabbit", event.getOrderId());
            assertEquals("user-rabbit", event.getUserId());
            assertEquals(0, new BigDecimal("268.00").compareTo(event.getPayAmount()));
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
}
