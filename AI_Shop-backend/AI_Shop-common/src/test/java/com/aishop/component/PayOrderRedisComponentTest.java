package com.aishop.component;

import com.aishop.support.PayOrderLifecycleLockHolder;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionSynchronizationUtils;

import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class PayOrderRedisComponentTest {

    @Mock
    private StringRedisTemplate redisTemplate;
    @Mock
    private ValueOperations<String, String> valueOperations;

    private PayOrderRedisComponent component;

    @BeforeEach
    void setUp() {
        component = new PayOrderRedisComponent();
        ReflectionTestUtils.setField(component, "stringRedisTemplate", redisTemplate);
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.setIfAbsent(any(), any(), anyLong(), eq(TimeUnit.SECONDS)))
                .thenReturn(true);
        when(redisTemplate.execute(any(), any(), any())).thenReturn(1L);
    }

    @AfterEach
    void clearTransactionState() {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.clearSynchronization();
        }
        TransactionSynchronizationManager.setActualTransactionActive(false);
        PayOrderLifecycleLockHolder.clear();
    }

    @Test
    void transactionKeepsLifecycleLockUntilAfterCompletion() {
        TransactionSynchronizationManager.initSynchronization();
        TransactionSynchronizationManager.setActualTransactionActive(true);

        component.runWithPayOrderLifecycleLock(
                "pay-1", () -> assertTrue(PayOrderLifecycleLockHolder.isBound()));

        assertTrue(PayOrderLifecycleLockHolder.isBound());
        verify(redisTemplate, never()).execute(any(), any(), any());

        TransactionSynchronizationUtils.invokeAfterCompletion(
                TransactionSynchronizationManager.getSynchronizations(),
                TransactionSynchronization.STATUS_COMMITTED);

        assertFalse(PayOrderLifecycleLockHolder.isBound());
        verify(redisTemplate).execute(any(), any(), any());
    }

    @Test
    void callWithoutTransactionReleasesLifecycleLockImmediately() {
        component.runWithPayOrderLifecycleLock(
                "pay-2", () -> assertTrue(PayOrderLifecycleLockHolder.isBound()));

        assertFalse(PayOrderLifecycleLockHolder.isBound());
        verify(redisTemplate).execute(any(), any(), any());
    }
}
