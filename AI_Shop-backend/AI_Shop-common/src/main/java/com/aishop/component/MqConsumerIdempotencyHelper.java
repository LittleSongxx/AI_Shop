package com.aishop.component;

import com.aishop.constants.Constants;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

@Slf4j
@Component
public class MqConsumerIdempotencyHelper {

    public static final String HEADER_IDEMPOTENCY_KEY = "x-idempotency-key";
    public static final String HEADER_CONSUMER_LEASE_TOKEN = "x-aishop-consumer-lease-token";
    public static final String HEADER_CONSUMER_CLAIM_RESULT = "x-aishop-consumer-claim-result";

    public enum ClaimResult {
        ACQUIRED,
        COMPLETED,
        BUSY
    }

    private static final DefaultRedisScript<Long> RELEASE_LEASE_SCRIPT = new DefaultRedisScript<>(
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
                    + "return redis.call('DEL', KEYS[1]) "
                    + "else return 0 end",
            Long.class);

    private static final DefaultRedisScript<Long> COMPLETE_LEASE_SCRIPT = new DefaultRedisScript<>(
            "local current = redis.call('GET', KEYS[1]); "
                    + "if current ~= ARGV[1] then return 0 end; "
                    + "redis.call('SET', KEYS[2], '1', 'EX', ARGV[2]); "
                    + "redis.call('DEL', KEYS[1]); "
                    + "return 1",
            Long.class);

    @Resource
    private StringRedisTemplate stringRedisTemplate;

    @Value("${mq.consume.processing-lease-seconds:300}")
    private long processingLeaseSeconds;

    public boolean tryBeginConsume(Message message, long ttlSeconds) {
        ClaimResult result;
        try {
            result = claimConsume(message);
        } catch (RuntimeException exception) {
            String queueName = resolveQueueName(message);
            String key = resolveIdempotencyKey(message);
            log.error(
                    "MQ 消费幂等存储不可用，消息将延后重试, queue={}, key={}",
                    queueName,
                    key,
                    exception);
            result = ClaimResult.BUSY;
        }
        message.getMessageProperties().setHeader(HEADER_CONSUMER_CLAIM_RESULT, result.name());
        return result == ClaimResult.ACQUIRED;
    }

    public ClaimResult resolveClaimResult(Message message) {
        if (message == null || message.getMessageProperties() == null) {
            return ClaimResult.COMPLETED;
        }
        Object value = message.getMessageProperties().getHeader(HEADER_CONSUMER_CLAIM_RESULT);
        if (value == null) {
            return ClaimResult.COMPLETED;
        }
        try {
            return ClaimResult.valueOf(String.valueOf(value));
        } catch (IllegalArgumentException ignored) {
            return ClaimResult.COMPLETED;
        }
    }

    private ClaimResult claimConsume(Message message) {
        String key = resolveIdempotencyKey(message);
        if (StringTools.isEmpty(key)) {
            return ClaimResult.ACQUIRED;
        }
        String queueName = resolveQueueName(message);
        String doneKey = doneKey(queueName, key);
        if (Boolean.TRUE.equals(stringRedisTemplate.hasKey(doneKey))) {
            log.debug("MQ 消费幂等跳过已完成消息, queue={}, key={}", queueName, key);
            return ClaimResult.COMPLETED;
        }
        String processingKey = processingKey(queueName, key);
        String leaseToken = UUID.randomUUID().toString();
        Boolean acquired = stringRedisTemplate.opsForValue()
                .setIfAbsent(processingKey, leaseToken,
                        Math.max(30L, processingLeaseSeconds), TimeUnit.SECONDS);
        if (!Boolean.TRUE.equals(acquired)) {
            log.debug("MQ 消费租约占用, queue={}, key={}", queueName, key);
            return ClaimResult.BUSY;
        }
        message.getMessageProperties().setHeader(HEADER_CONSUMER_LEASE_TOKEN, leaseToken);
        if (Boolean.TRUE.equals(stringRedisTemplate.hasKey(doneKey))) {
            releaseLease(processingKey, leaseToken);
            message.getMessageProperties().getHeaders().remove(HEADER_CONSUMER_LEASE_TOKEN);
            return ClaimResult.COMPLETED;
        }
        return ClaimResult.ACQUIRED;
    }

    public void releaseConsume(Message message) {
        String key = resolveIdempotencyKey(message);
        if (StringTools.isEmpty(key)) {
            return;
        }
        String leaseToken = resolveLeaseToken(message);
        if (StringTools.isEmpty(leaseToken)) {
            return;
        }
        try {
            releaseLease(processingKey(resolveQueueName(message), key), leaseToken);
        } catch (RuntimeException exception) {
            log.warn(
                    "MQ 消费租约释放失败，将等待租约自然过期, queue={}, key={}",
                    resolveQueueName(message),
                    key,
                    exception);
        } finally {
            message.getMessageProperties().getHeaders().remove(HEADER_CONSUMER_LEASE_TOKEN);
        }
    }

    public void markCompleted(String queueName, Message message, long ttlSeconds) {
        String key = resolveIdempotencyKey(message);
        if (StringTools.isEmpty(key)) {
            return;
        }
        String resolvedQueue = StringTools.isEmpty(queueName) ? resolveQueueName(message) : queueName;
        String doneKey = doneKey(resolvedQueue, key);
        String processingKey = processingKey(resolvedQueue, key);
        String leaseToken = resolveLeaseToken(message);
        long completedTtlSeconds = Math.max(60L, ttlSeconds);
        if (StringTools.isEmpty(leaseToken)) {
            stringRedisTemplate.opsForValue().set(
                    doneKey, "1", completedTtlSeconds, TimeUnit.SECONDS);
            return;
        }
        Long completed = stringRedisTemplate.execute(
                COMPLETE_LEASE_SCRIPT,
                List.of(processingKey, doneKey),
                leaseToken,
                String.valueOf(completedTtlSeconds));
        if (!Long.valueOf(1L).equals(completed)) {
            log.warn("MQ 消费完成标记被拒绝，处理租约已转移, queue={}, key={}",
                    resolvedQueue, key);
        }
        message.getMessageProperties().getHeaders().remove(HEADER_CONSUMER_LEASE_TOKEN);
    }

    public String resolveIdempotencyKey(Message message) {
        if (message == null || message.getMessageProperties() == null) {
            return null;
        }
        Object header = message.getMessageProperties().getHeader(HEADER_IDEMPOTENCY_KEY);
        if (header != null && !StringTools.isEmpty(String.valueOf(header))) {
            return String.valueOf(header);
        }
        return message.getMessageProperties().getMessageId();
    }

    private String resolveQueueName(Message message) {
        if (message != null && message.getMessageProperties() != null
                && !StringTools.isEmpty(message.getMessageProperties().getConsumerQueue())) {
            return message.getMessageProperties().getConsumerQueue();
        }
        return "unknown";
    }

    private String resolveLeaseToken(Message message) {
        if (message == null || message.getMessageProperties() == null) {
            return null;
        }
        Object token = message.getMessageProperties().getHeader(HEADER_CONSUMER_LEASE_TOKEN);
        return token == null ? null : String.valueOf(token);
    }

    private void releaseLease(String processingKey, String leaseToken) {
        stringRedisTemplate.execute(RELEASE_LEASE_SCRIPT, List.of(processingKey), leaseToken);
    }

    private String processingKey(String queueName, String key) {
        return Constants.REDIS_KEY_MQ_CONSUME_IDEMPOTENT + "processing:" + queueName + ":" + key;
    }

    private String doneKey(String queueName, String key) {
        return Constants.REDIS_KEY_MQ_CONSUME_IDEMPOTENT + "done:" + queueName + ":" + key;
    }
}
