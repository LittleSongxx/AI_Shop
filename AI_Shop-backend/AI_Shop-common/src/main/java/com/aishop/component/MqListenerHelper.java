package com.aishop.component;

import com.aishop.constants.RabbitMQConfig;
import com.aishop.utils.StringTools;
import com.rabbitmq.client.Channel;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.core.MessagePropertiesBuilder;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.UUID;

@Slf4j
@Component
public class MqListenerHelper {

    public static final int DEFAULT_MAX_CONSUME_RETRIES = 3;

    public static final long CONSUME_IDEMPOTENCY_TTL_STANDARD_SECONDS = 24 * 3600L;

    public static final long CONSUME_IDEMPOTENCY_TTL_HIGH_SECONDS = 7 * 24 * 3600L;
    public static final String HEADER_RETRY_ATTEMPT = "x-aishop-retry-attempt";
    private static final int BUSY_RETRY_QUEUE_ATTEMPT = 2;

    @Resource
    private MqConsumerIdempotencyHelper mqConsumerIdempotencyHelper;
    @Resource
    private MqConsumeFailureRecorder mqConsumeFailureRecorder;
    @Resource
    private MqPublisherConfirmHelper mqPublisherConfirmHelper;

    public boolean tryBeginConsume(Message message, long ttlSeconds) {
        return mqConsumerIdempotencyHelper.tryBeginConsume(message, ttlSeconds);
    }

    public void ackCompletedOrDeferBusy(Channel channel, long deliveryTag, Message message,
                                        String queueName) throws IOException {
        if (mqConsumerIdempotencyHelper.resolveClaimResult(message)
                != MqConsumerIdempotencyHelper.ClaimResult.BUSY) {
            channel.basicAck(deliveryTag, false);
            return;
        }
        String idempotencyKey = resolveIdempotencyKey(message);
        try {
            Message deferredMessage = cloneForBusyDeferral(message, queueName);
            mqPublisherConfirmHelper.sendRawAndAwaitConfirm(
                    RabbitMQConfig.MQ_RETRY_EXCHANGE,
                    RabbitMQConfig.retryRoutingKey(queueName, BUSY_RETRY_QUEUE_ATTEMPT),
                    deferredMessage,
                    "busy:" + queueName + ":" + idempotencyKey + ":" + UUID.randomUUID());
            channel.basicAck(deliveryTag, false);
            log.debug("MQ 消费租约占用，消息已延后重试 queue={}, key={}", queueName, idempotencyKey);
        } catch (Exception publishError) {
            log.error("MQ 消费租约占用消息延后失败 queue={}, key={}",
                    queueName, idempotencyKey, publishError);
            channel.basicNack(deliveryTag, false, true);
        }
    }

    public void releaseConsume(Message message) {
        mqConsumerIdempotencyHelper.releaseConsume(message);
    }

    public String resolveIdempotencyKey(Message message) {
        return mqConsumerIdempotencyHelper.resolveIdempotencyKey(message);
    }

    public void nackWithRetryOrDlq(Channel channel, long deliveryTag, Message message,
                                   String queueName, Object payload, Exception error) throws IOException {
        String idempotencyKey = resolveIdempotencyKey(message);
        if (StringTools.isEmpty(idempotencyKey)) {
            mqConsumeFailureRecorder.record(queueName, message, payload, error);
            publishFailureOrNack(
                    channel,
                    deliveryTag,
                    message,
                    queueName,
                    "missing-id:" + queueName + ":" + UUID.randomUUID(),
                    1,
                    error);
            return;
        }
        int attempt = retryAttempt(message) + 1;
        releaseConsume(message);
        if (attempt <= DEFAULT_MAX_CONSUME_RETRIES) {
            log.warn("MQ 消费失败进入延迟重试 queue={}, key={}, attempt={}/{}",
                    queueName, idempotencyKey, attempt, DEFAULT_MAX_CONSUME_RETRIES);
            try {
                Message retryMessage = cloneForRetry(message, attempt, queueName, error);
                mqPublisherConfirmHelper.sendRawAndAwaitConfirm(
                        RabbitMQConfig.MQ_RETRY_EXCHANGE,
                        RabbitMQConfig.retryRoutingKey(queueName, attempt),
                        retryMessage,
                        "retry:" + queueName + ":" + idempotencyKey + ":" + attempt + ":" + UUID.randomUUID());
                channel.basicAck(deliveryTag, false);
            } catch (Exception publishError) {
                log.error("MQ 延迟重试消息发布失败 queue={}, key={}", queueName, idempotencyKey, publishError);
                mqConsumeFailureRecorder.record(queueName, message, payload, publishError);
                channel.basicNack(deliveryTag, false, false);
            }
            return;
        }
        mqConsumeFailureRecorder.record(queueName, message, payload, error);
        publishFailureOrNack(
                channel,
                deliveryTag,
                message,
                queueName,
                "failed:" + queueName + ":" + idempotencyKey + ":" + UUID.randomUUID(),
                attempt,
                error);
    }

    private void publishFailureOrNack(Channel channel, long deliveryTag, Message message,
                                      String queueName, String correlationId, int attempt,
                                      Exception error) throws IOException {
        try {
            Message failedMessage = cloneForRetry(message, attempt, queueName, error);
            mqPublisherConfirmHelper.sendRawAndAwaitConfirm(
                    RabbitMQConfig.MQ_FAILURE_EXCHANGE,
                    RabbitMQConfig.MQ_FAILURE_KEY,
                    failedMessage,
                    correlationId);
            channel.basicAck(deliveryTag, false);
        } catch (Exception publishError) {
            log.error("MQ 最终失败消息发布失败 queue={}, correlationId={}",
                    queueName, correlationId, publishError);
            channel.basicNack(deliveryTag, false, false);
        }
    }

    private int retryAttempt(Message message) {
        Object value = message.getMessageProperties().getHeaders().get(HEADER_RETRY_ATTEMPT);
        if (value instanceof Number number) {
            return number.intValue();
        }
        try {
            return value == null ? 0 : Integer.parseInt(String.valueOf(value));
        } catch (NumberFormatException ignored) {
            return 0;
        }
    }

    private Message cloneForRetry(Message source, int attempt, String queueName, Exception error) {
        MessageProperties properties = MessagePropertiesBuilder
                .fromClonedProperties(source.getMessageProperties())
                .setHeader(HEADER_RETRY_ATTEMPT, attempt)
                .setHeader("x-aishop-original-queue", queueName)
                .setHeader("x-aishop-last-error", truncate(error == null ? null : error.getMessage()))
                .setDeliveryTag(0L)
                .build();
        properties.getHeaders().remove(MqConsumerIdempotencyHelper.HEADER_CONSUMER_LEASE_TOKEN);
        properties.getHeaders().remove(MqConsumerIdempotencyHelper.HEADER_CONSUMER_CLAIM_RESULT);
        properties.setConsumerQueue(null);
        properties.setReceivedExchange(null);
        properties.setReceivedRoutingKey(null);
        return new Message(source.getBody(), properties);
    }

    private Message cloneForBusyDeferral(Message source, String queueName) {
        MessageProperties properties = MessagePropertiesBuilder
                .fromClonedProperties(source.getMessageProperties())
                .setHeader("x-aishop-original-queue", queueName)
                .setDeliveryTag(0L)
                .build();
        properties.getHeaders().remove(MqConsumerIdempotencyHelper.HEADER_CONSUMER_LEASE_TOKEN);
        properties.getHeaders().remove(MqConsumerIdempotencyHelper.HEADER_CONSUMER_CLAIM_RESULT);
        properties.setConsumerQueue(null);
        properties.setReceivedExchange(null);
        properties.setReceivedRoutingKey(null);
        return new Message(source.getBody(), properties);
    }

    private String truncate(String value) {
        if (value == null) {
            return "";
        }
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        if (bytes.length <= 500) {
            return value;
        }
        int end = 500;
        while (end > 0 && (bytes[end] & 0xC0) == 0x80) {
            end--;
        }
        return new String(bytes, 0, end, StandardCharsets.UTF_8);
    }

    public void clearConsumeRetry(String queueName, Message message) {
        mqConsumerIdempotencyHelper.markCompleted(
                queueName, message, CONSUME_IDEMPOTENCY_TTL_HIGH_SECONDS);
    }
}
