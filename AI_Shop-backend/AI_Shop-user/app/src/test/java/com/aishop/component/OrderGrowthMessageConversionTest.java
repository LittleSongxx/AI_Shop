package com.aishop.component;

import com.aishop.api.dto.OrderGrowthEventDTO;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;

class OrderGrowthMessageConversionTest {

    @Test
    void outboxMapPayloadConvertsToGrowthEventDto() {
        ObjectMapper mapper = new ObjectMapper();
        Map<String, Object> outboxPayload = mapper.convertValue(
                new OrderGrowthEventDTO("order-1", "user-1", new BigDecimal("268.00")),
                LinkedHashMap.class);
        Jackson2JsonMessageConverter converter = new Jackson2JsonMessageConverter(mapper);

        Message message = converter.toMessage(outboxPayload, new MessageProperties());
        message.getMessageProperties().setInferredArgumentType(OrderGrowthEventDTO.class);
        Object converted = converter.fromMessage(message);

        OrderGrowthEventDTO event = assertInstanceOf(OrderGrowthEventDTO.class, converted);
        assertEquals("order-1", event.getOrderId());
        assertEquals("user-1", event.getUserId());
        assertEquals(0, new BigDecimal("268.00").compareTo(event.getPayAmount()));
    }
}
