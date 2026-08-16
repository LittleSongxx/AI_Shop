package com.aishop.task;

import com.aishop.component.RedisComponent;
import com.aishop.constants.Constants;
import com.aishop.service.MqCompensationLogService;
import com.aishop.service.OutboxMessageService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.concurrent.TimeUnit;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class MqSchedulingLockScopeTest {

    private RedisComponent redisComponent;

    @BeforeEach
    void setUp() {
        redisComponent = mock(RedisComponent.class);
    }

    @Test
    void outboxLockIsScopedToTheOwningServiceDatabase() {
        OutboxMessageService service = mock(OutboxMessageService.class);
        OutboxDispatchTask task = new OutboxDispatchTask();
        ReflectionTestUtils.setField(task, "redisComponent", redisComponent);
        ReflectionTestUtils.setField(task, "outboxMessageService", service);
        ReflectionTestUtils.setField(task, "batchSize", 30);
        ReflectionTestUtils.setField(task, "maxRetries", 10);
        ReflectionTestUtils.setField(task, "applicationName", "aishop-product");
        String expectedKey = Constants.REDIS_KEY_MQ_COMPENSATE
                + "outbox:dispatch:lock:aishop-product";
        when(redisComponent.setIfAbsent(expectedKey, "1", 4, TimeUnit.SECONDS))
                .thenReturn(true);

        task.dispatch();

        verify(service).dispatchPendingBatch(30, 10);
    }

    @Test
    void compensationLockIsScopedToTheOwningServiceDatabase() {
        MqCompensationLogService service = mock(MqCompensationLogService.class);
        MqCompensationAutoReplayTask task = new MqCompensationAutoReplayTask();
        ReflectionTestUtils.setField(task, "redisComponent", redisComponent);
        ReflectionTestUtils.setField(task, "mqCompensationLogService", service);
        ReflectionTestUtils.setField(task, "batchSize", 10);
        ReflectionTestUtils.setField(task, "maxRetries", 5);
        ReflectionTestUtils.setField(task, "applicationName", "aishop-user");
        String expectedKey = Constants.REDIS_KEY_MQ_COMPENSATE_AUTO_REPLAY_LOCK
                + ":aishop-user";
        when(redisComponent.setIfAbsent(expectedKey, "1", 55, TimeUnit.SECONDS))
                .thenReturn(true);

        task.autoReplay();

        verify(service).autoReplayPendingSendFailures(10, 5);
    }
}
