package com.aishop.component;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.amqp.AmqpException;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.core.ReturnedMessage;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Collections;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;

@ExtendWith(MockitoExtension.class)
class MqPublisherConfirmHelperTest {

    @Mock
    private RabbitTemplate rabbitTemplate;
    @InjectMocks
    private MqPublisherConfirmHelper helper;

    @BeforeEach
    void setUp() {
        ReflectionTestUtils.setField(helper, "defaultConfirmTimeoutMs", 1_000L);
        ReflectionTestUtils.setField(helper, "confirmTimeoutByExchange", Collections.emptyMap());
    }

    @Test
    void rawMessageSucceedsOnlyAfterBrokerAck() {
        Message message = message();
        doAnswer(invocation -> {
            CorrelationData correlationData = invocation.getArgument(3);
            correlationData.getFuture().complete(new CorrelationData.Confirm(true, null));
            return null;
        }).when(rabbitTemplate).send(eq("exchange"), eq("route"), eq(message), any(CorrelationData.class));

        assertDoesNotThrow(() ->
                helper.sendRawAndAwaitConfirm("exchange", "route", message, "correlation-1"));
    }

    @Test
    void brokerNackFailsTheSend() {
        Message message = message();
        doAnswer(invocation -> {
            CorrelationData correlationData = invocation.getArgument(3);
            correlationData.getFuture().complete(new CorrelationData.Confirm(false, "disk alarm"));
            return null;
        }).when(rabbitTemplate).send(eq("exchange"), eq("route"), eq(message), any(CorrelationData.class));

        assertThrows(
                AmqpException.class,
                () -> helper.sendRawAndAwaitConfirm(
                        "exchange", "route", message, "correlation-2"));
    }

    @Test
    void mandatoryReturnFailsEvenWhenExchangeAcknowledgesPublish() {
        Message message = message();
        doAnswer(invocation -> {
            CorrelationData correlationData = invocation.getArgument(3);
            correlationData.setReturned(new ReturnedMessage(
                    message, 312, "NO_ROUTE", "exchange", "missing.route"));
            correlationData.getFuture().complete(new CorrelationData.Confirm(true, null));
            return null;
        }).when(rabbitTemplate).send(
                eq("exchange"), eq("missing.route"), eq(message), any(CorrelationData.class));

        assertThrows(
                AmqpException.class,
                () -> helper.sendRawAndAwaitConfirm(
                        "exchange", "missing.route", message, "correlation-3"));
    }

    private static Message message() {
        return new Message("payload".getBytes(), new MessageProperties());
    }
}
