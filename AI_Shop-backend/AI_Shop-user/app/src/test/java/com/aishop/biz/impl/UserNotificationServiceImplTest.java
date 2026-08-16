package com.aishop.biz.impl;

import com.aishop.component.NotifyPushPublisher;
import com.aishop.component.RedisComponent;
import com.aishop.constants.Constants;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.TransactionalMqSender;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.po.UserNotification;
import com.aishop.entity.query.UserNotificationQuery;
import com.aishop.mappers.UserNotificationMapper;
import com.aishop.redis.RedisUtils;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionSynchronizationUtils;

import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class UserNotificationServiceImplTest {

    @Mock
    private UserNotificationMapper<UserNotification, UserNotificationQuery> userNotificationMapper;
    @Mock
    private RedisComponent redisComponent;
    @Mock
    private TransactionalMqSender transactionalMqSender;
    @Mock
    private RedisUtils redisUtils;
    @Mock
    private StringRedisTemplate stringRedisTemplate;
    @Mock
    private ValueOperations<String, String> valueOperations;
    @Mock
    private NotifyPushPublisher notifyPushPublisher;
    @InjectMocks
    private UserNotificationServiceImpl service;

    @AfterEach
    void clearTransactionSynchronization() {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.clearSynchronization();
        }
    }

    @Test
    void unreadCountCachesDatabaseResultWithShortTtl() {
        String key = Constants.REDIS_KEY_USER_UNREAD_COUNT + "u1";
        when(stringRedisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.get(key)).thenReturn(null);
        when(userNotificationMapper.selectCount(any())).thenReturn(7);

        assertEquals(7, service.countUnread("u1"));

        verify(valueOperations).set(key, "7", 30L, TimeUnit.SECONDS);
    }

    @Test
    void readMutationInvalidatesUnreadCacheOnlyAfterCommit() {
        UserNotification notification = notification("N1", "u1");
        notification.setReadStatus(0);
        when(userNotificationMapper.selectByNotificationId("N1")).thenReturn(notification);
        TransactionSynchronizationManager.initSynchronization();

        service.markRead("u1", "N1");

        verify(redisComponent, never()).deleteCounter(any());
        TransactionSynchronizationUtils.invokeAfterCommit(
                TransactionSynchronizationManager.getSynchronizations());
        verify(redisComponent).deleteCounter(Constants.REDIS_KEY_USER_UNREAD_COUNT + "u1");
    }

    @Test
    void asyncNotificationUsesTransactionalOutboxWithoutRedisGate() {
        service.sendAsync("u1", "订单已发货", "物流单号 123", "logistics", "o1");

        verify(transactionalMqSender).sendAfterCommit(
                eq(RabbitMQConfig.NOTIFY_EXCHANGE),
                eq(RabbitMQConfig.NOTIFY_KEY),
                any(),
                eq("notify:u1:logistics:o1"),
                eq(MessageReliabilityLevelEnum.HIGH));
        verify(redisComponent, never()).setIfAbsent(any(), any(), anyLong(), any());
    }

    @Test
    void directNotificationPushesAfterCommitAndReleasesDedupOnRollback() {
        String dedupKey = Constants.REDIS_KEY_NOTIFY_DEDUP + "u1:order:o1:订单更新";
        when(redisComponent.setIfAbsent(dedupKey, "1", 24, TimeUnit.HOURS)).thenReturn(true);
        TransactionSynchronizationManager.initSynchronization();

        service.send("u1", "订单更新", "已发货", "order", "o1");

        verify(userNotificationMapper).insert(any());
        verify(notifyPushPublisher, never()).push(any());
        TransactionSynchronizationUtils.invokeAfterCompletion(
                TransactionSynchronizationManager.getSynchronizations(),
                TransactionSynchronization.STATUS_ROLLED_BACK);
        verify(stringRedisTemplate).delete(dedupKey);
        verify(notifyPushPublisher, never()).push(any());
    }

    private static UserNotification notification(String notificationId, String userId) {
        UserNotification notification = new UserNotification();
        notification.setNotificationId(notificationId);
        notification.setUserId(userId);
        return notification;
    }
}
