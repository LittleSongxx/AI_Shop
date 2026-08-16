package com.aishop.component;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.MessagePostProcessor;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.ReturnedMessage;
import org.springframework.amqp.rabbit.connection.CachingConnectionFactory;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.Collections;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

@Slf4j
@Component
public class MqPublisherConfirmHelper implements RabbitTemplate.ConfirmCallback, RabbitTemplate.ReturnsCallback {

    @Value("${mq.publisher.confirm-timeout-ms:5000}")
    private long defaultConfirmTimeoutMs;

    @Value("#{${mq.publisher.confirm-timeout-by-exchange:{}}}")
    private Map<String, Long> confirmTimeoutByExchange = Collections.emptyMap();

    @Resource
    private RabbitTemplate rabbitTemplate;

    @PostConstruct
    public void attachCallbacks() {
        rabbitTemplate.setConfirmCallback(this);
        rabbitTemplate.setReturnsCallback(this);
        rabbitTemplate.setMandatory(true);
        logPublisherConfirmConfig();
    }

    private void logPublisherConfirmConfig() {
        if (rabbitTemplate.getConnectionFactory() instanceof CachingConnectionFactory factory) {
            if (!factory.isPublisherConfirms()) {
                log.error("RabbitMQ 未启用 Publisher Confirm（spring.rabbitmq.publisher-confirm-type），"
                        + "Confirm 回调不会触发，所有发送将超时失败");
            } else {
                log.info("RabbitMQ Publisher Confirm 已启用, defaultTimeoutMs={}, exchangeOverrides={}",
                        defaultConfirmTimeoutMs, confirmTimeoutByExchange);
            }
        }
    }

    private long resolveConfirmTimeoutMs(String exchange) {
        if (exchange != null && confirmTimeoutByExchange.containsKey(exchange)) {
            return confirmTimeoutByExchange.get(exchange);
        }
        return defaultConfirmTimeoutMs;
    }

    @Override
    public void confirm(CorrelationData correlationData, boolean ack, String cause) {
        if (correlationData == null || correlationData.getId() == null) {
            return;
        }
        if (!ack) {
            log.warn("Publisher confirm NACK, id={}, cause={}", correlationData.getId(), cause);
        }
    }

    @Override
    public void returnedMessage(ReturnedMessage returned) {
        log.warn("Publisher return unroutable, messageId={}, exchange={}, routingKey={}, reply={}",
                returned.getMessage().getMessageProperties().getMessageId(),
                returned.getExchange(),
                returned.getRoutingKey(),
                returned.getReplyText());
    }

    public void sendAndAwaitConfirm(String exchange, String routingKey, Object body,
                                    MessagePostProcessor processor, String correlationId) throws Exception {
        long timeoutMs = resolveConfirmTimeoutMs(exchange);
        CorrelationData correlationData = new CorrelationData(correlationId);
        try {
            rabbitTemplate.convertAndSend(exchange, routingKey, body, processor, correlationData);
            awaitConfirm(correlationData, timeoutMs, correlationId, false);
        } catch (TimeoutException e) {
            throw new org.springframework.amqp.AmqpException(
                    "Publisher confirm 超时, key=" + correlationId + ", timeoutMs=" + timeoutMs, e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new org.springframework.amqp.AmqpException(
                    "Publisher confirm 等待被中断, key=" + correlationId, e);
        }
    }

    public void sendRawAndAwaitConfirm(String exchange, String routingKey, Message message,
                                       String correlationId) throws Exception {
        long timeoutMs = resolveConfirmTimeoutMs(exchange);
        CorrelationData correlationData = new CorrelationData(correlationId);
        try {
            rabbitTemplate.send(exchange, routingKey, message, correlationData);
            awaitConfirm(correlationData, timeoutMs, correlationId, true);
        } catch (TimeoutException e) {
            throw new org.springframework.amqp.AmqpException(
                    "Publisher confirm 超时, key=" + correlationId + ", timeoutMs=" + timeoutMs, e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new org.springframework.amqp.AmqpException(
                    "Publisher confirm 等待被中断, key=" + correlationId, e);
        }
    }

    private void awaitConfirm(CorrelationData correlationData, long timeoutMs, String correlationId,
                              boolean rawMessage) throws Exception {
        CorrelationData.Confirm confirm = correlationData.getFuture().get(timeoutMs, TimeUnit.MILLISECONDS);
        ReturnedMessage returned = correlationData.getReturned();
        if (returned != null) {
            throw new org.springframework.amqp.AmqpException(
                    "消息无法路由, key=" + correlationId
                            + ", exchange=" + returned.getExchange()
                            + ", routingKey=" + returned.getRoutingKey()
                            + ", reply=" + returned.getReplyText());
        }
        if (confirm == null || !confirm.isAck()) {
            String prefix = rawMessage ? "Broker 未确认原始消息持久化" : "Broker 未确认消息持久化";
            String reason = confirm == null ? null : confirm.getReason();
            throw new org.springframework.amqp.AmqpException(
                    prefix + ", key=" + correlationId + (reason == null ? "" : ", cause=" + reason));
        }
    }
}
