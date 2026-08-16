package com.aishop.component;

import com.aishop.constants.Constants;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class MqConsumerIdempotencyHelperTest {

    @Mock
    private StringRedisTemplate redisTemplate;
    @Mock
    private ValueOperations<String, String> valueOperations;

    private MqConsumerIdempotencyHelper helper;

    @BeforeEach
    void setUp() {
        helper = new MqConsumerIdempotencyHelper();
        ReflectionTestUtils.setField(helper, "stringRedisTemplate", redisTemplate);
        ReflectionTestUtils.setField(helper, "processingLeaseSeconds", 300L);
        lenient().when(redisTemplate.opsForValue()).thenReturn(valueOperations);
    }

    @Test
    void acquiredLeaseCarriesOwnerTokenAndReleaseUsesAtomicScript() {
        Message message = message("queue-a", "message-1");
        when(redisTemplate.hasKey(any())).thenReturn(false);
        when(valueOperations.setIfAbsent(any(), any(), anyLong(), eq(TimeUnit.SECONDS)))
                .thenReturn(true);

        assertTrue(helper.tryBeginConsume(message, 3_600L));
        String token = message.getMessageProperties()
                .getHeader(MqConsumerIdempotencyHelper.HEADER_CONSUMER_LEASE_TOKEN);
        assertNotNull(token);

        helper.releaseConsume(message);

        assertNull(message.getMessageProperties()
                .getHeader(MqConsumerIdempotencyHelper.HEADER_CONSUMER_LEASE_TOKEN));
        verify(redisTemplate).execute(any(), any(), eq(token));
    }

    @Test
    void completionWithoutLeaseStillWritesQueueScopedDoneMarker() {
        Message message = message("queue-b", "message-2");

        helper.markCompleted("queue-b", message, 10L);

        verify(valueOperations).set(
                eq(Constants.REDIS_KEY_MQ_CONSUME_IDEMPOTENT + "done:queue-b:message-2"),
                eq("1"),
                eq(60L),
                eq(TimeUnit.SECONDS));
    }

    @Test
    void completionScriptChecksLeaseBeforeWritingDoneMarker() {
        DefaultRedisScript<?> script = (DefaultRedisScript<?>) ReflectionTestUtils.getField(
                MqConsumerIdempotencyHelper.class, "COMPLETE_LEASE_SCRIPT");
        assertNotNull(script);
        String source = script.getScriptAsString();

        int ownershipCheck = source.indexOf("if current ~= ARGV[1] then return 0 end");
        int doneWrite = source.indexOf("redis.call('SET', KEYS[2]");
        assertTrue(ownershipCheck >= 0);
        assertTrue(doneWrite > ownershipCheck);
        assertFalse(source.substring(0, ownershipCheck).contains("KEYS[2]"));
    }

    @Test
    void redeliveredMessageDoesNotStealAnActiveProcessingLease() {
        Message message = message("queue-c", "message-3");
        message.getMessageProperties().setRedelivered(true);
        when(redisTemplate.hasKey(any())).thenReturn(false);
        when(valueOperations.setIfAbsent(any(), any(), anyLong(), eq(TimeUnit.SECONDS)))
                .thenReturn(false);

        assertFalse(helper.tryBeginConsume(message, 3_600L));

        assertEquals(
                MqConsumerIdempotencyHelper.ClaimResult.BUSY,
                helper.resolveClaimResult(message));
        assertNull(message.getMessageProperties()
                .getHeader(MqConsumerIdempotencyHelper.HEADER_CONSUMER_LEASE_TOKEN));
    }

    @Test
    void freshDuplicateDoesNotStealAnActiveProcessingLease() {
        Message message = message("queue-d", "message-4");
        when(redisTemplate.hasKey(any())).thenReturn(false);
        when(valueOperations.setIfAbsent(any(), any(), anyLong(), eq(TimeUnit.SECONDS)))
                .thenReturn(false);

        assertFalse(helper.tryBeginConsume(message, 3_600L));
        assertEquals(
                MqConsumerIdempotencyHelper.ClaimResult.BUSY,
                helper.resolveClaimResult(message));
    }

    @Test
    void completedDuplicateIsDistinctFromAnActiveLease() {
        Message message = message("queue-e", "message-5");
        when(redisTemplate.hasKey(any())).thenReturn(true);

        assertFalse(helper.tryBeginConsume(message, 3_600L));
        assertEquals(
                MqConsumerIdempotencyHelper.ClaimResult.COMPLETED,
                helper.resolveClaimResult(message));
    }

    @Test
    void redisClaimFailureDefersMessageInsteadOfEscapingTheListener() {
        Message message = message("queue-f", "message-6");
        when(redisTemplate.hasKey(any()))
                .thenThrow(new IllegalStateException("redis unavailable"));

        assertFalse(helper.tryBeginConsume(message, 3_600L));

        assertEquals(
                MqConsumerIdempotencyHelper.ClaimResult.BUSY,
                helper.resolveClaimResult(message));
    }

    @Test
    void redisReleaseFailureDoesNotBlockRabbitRetryPublishing() {
        Message message = message("queue-g", "message-7");
        message.getMessageProperties().setHeader(
                MqConsumerIdempotencyHelper.HEADER_CONSUMER_LEASE_TOKEN,
                "lease-7");
        doThrow(new IllegalStateException("redis unavailable"))
                .when(redisTemplate)
                .execute(any(), any(), eq("lease-7"));

        helper.releaseConsume(message);

        assertNull(message.getMessageProperties()
                .getHeader(MqConsumerIdempotencyHelper.HEADER_CONSUMER_LEASE_TOKEN));
    }

    private static Message message(String queue, String idempotencyKey) {
        MessageProperties properties = new MessageProperties();
        properties.setConsumerQueue(queue);
        properties.setHeader(MqConsumerIdempotencyHelper.HEADER_IDEMPOTENCY_KEY, idempotencyKey);
        return new Message(new byte[0], properties);
    }
}
