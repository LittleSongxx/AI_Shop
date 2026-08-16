package com.aishop.biz.impl;

import com.aishop.api.dto.NotificationMessageDTO;
import com.aishop.entity.enums.PageSize;
import com.aishop.entity.po.UserNotification;
import com.aishop.entity.query.SimplePage;
import com.aishop.entity.query.UserNotificationQuery;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.UserNotificationMapper;
import com.aishop.component.NotifyPushPublisher;
import com.aishop.component.RedisComponent;
import com.aishop.constants.Constants;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.TransactionalMqSender;
import com.aishop.support.MqIdempotencyKeys;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.redis.RedisUtils;
import com.aishop.biz.UserNotificationService;
import com.aishop.utils.StringTools;
import org.springframework.data.redis.core.StringRedisTemplate;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.concurrent.TimeUnit;

@Service("userNotificationService")
@Slf4j
public class UserNotificationServiceImpl implements UserNotificationService {

    private static final long UNREAD_CACHE_TTL_SECONDS = 30L;

    @Resource
    private UserNotificationMapper<UserNotification, UserNotificationQuery> userNotificationMapper;
    @Resource
    private RedisComponent redisComponent;
    @Resource
    private TransactionalMqSender transactionalMqSender;
    @Resource
    private RedisUtils redisUtils;
    @Resource
    private StringRedisTemplate stringRedisTemplate;
    @Resource
    private NotifyPushPublisher notifyPushPublisher;

    @Override
    public PaginationResultVO<UserNotification> loadPage(String userId, Integer pageNo, Integer readStatus) {
        UserNotificationQuery query = new UserNotificationQuery();
        query.setUserId(userId);
        query.setPageNo(pageNo);
        query.setOrderBy(com.aishop.entity.query.SafeSort.of("create_time desc"));
        if (readStatus != null) {
            query.setReadStatus(readStatus);
        }
        int count = userNotificationMapper.selectCount(query);
        int pageSize = PageSize.SIZE15.getSize();
        SimplePage page = new SimplePage(pageNo, count, pageSize);
        query.setSimplePage(page);
        List<UserNotification> list = userNotificationMapper.selectList(query);
        return new PaginationResultVO<>(count, page.getPageSize(), page.getPageNo(), page.getPageTotal(), list);
    }

    @Override
    public Integer countUnread(String userId) {
        return (int) getUnreadCountFromRedisOrSync(userId);
    }

    private String unreadCountKey(String userId) {
        return Constants.REDIS_KEY_USER_UNREAD_COUNT + userId;
    }

    private void invalidateUnreadCache(String userId) {
        try {
            redisComponent.deleteCounter(unreadCountKey(userId));
        } catch (Exception e) {
            log.warn("未读通知缓存失效失败，缓存会在短 TTL 后自动校准, userId={}", userId, e);
        }
    }

    private void invalidateUnreadAfterCommit(String userId) {
        runAfterCommit(() -> invalidateUnreadCache(userId));
    }

    private long getUnreadCountFromRedisOrSync(String userId) {
        String key = unreadCountKey(userId);
        try {
            String cached = stringRedisTemplate.opsForValue().get(key);
            if (cached != null) {
                return Math.max(0, Long.parseLong(cached));
            }
        } catch (Exception e) {
            log.warn("读取未读通知缓存失败，将回源数据库, userId={}", userId, e);
        }
        UserNotificationQuery query = new UserNotificationQuery();
        query.setUserId(userId);
        query.setReadStatus(0);
        int dbCount = userNotificationMapper.selectCount(query);
        try {
            stringRedisTemplate.opsForValue().set(
                    key,
                    String.valueOf(dbCount),
                    UNREAD_CACHE_TTL_SECONDS,
                    TimeUnit.SECONDS);
        } catch (Exception e) {
            log.warn("写入未读通知缓存失败，不影响数据库计数结果, userId={}", userId, e);
        }
        return dbCount;
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void markRead(String userId, String notificationId) {
        UserNotification notification = userNotificationMapper.selectByNotificationId(notificationId);
        if (notification == null || !notification.getUserId().equals(userId)) {
            throw new BusinessException("消息不存在");
        }
        if (Integer.valueOf(1).equals(notification.getReadStatus())) {
            return;
        }
        UserNotification update = new UserNotification();
        update.setReadStatus(1);
        userNotificationMapper.updateByNotificationId(update, notificationId);
        invalidateUnreadAfterCommit(userId);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void markAllRead(String userId) {
        UserNotificationQuery query = new UserNotificationQuery();
        query.setUserId(userId);
        query.setReadStatus(0);
        UserNotification update = new UserNotification();
        update.setReadStatus(1);
        userNotificationMapper.updateByParam(update, query);
        invalidateUnreadAfterCommit(userId);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void delete(String userId, String notificationId) {
        UserNotification notification = userNotificationMapper.selectByNotificationId(notificationId);
        if (notification == null || !notification.getUserId().equals(userId)) {
            throw new BusinessException("消息不存在");
        }
        UserNotificationQuery query = new UserNotificationQuery();
        query.setUserId(userId);
        query.setNotificationId(notificationId);
        userNotificationMapper.deleteByParam(query);
        invalidateUnreadAfterCommit(userId);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void clearAll(String userId) {
        UserNotificationQuery query = new UserNotificationQuery();
        query.setUserId(userId);
        userNotificationMapper.deleteByParam(query);
        invalidateUnreadAfterCommit(userId);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void send(String userId, String title, String content, String bizType, String bizId) {
        if (StringTools.isEmpty(userId) || StringTools.isEmpty(title)) {
            return;
        }
        String dedupKey = Constants.REDIS_KEY_NOTIFY_DEDUP + userId + ":" + (bizType == null ? "" : bizType) + ":"
                + (bizId == null ? "" : bizId) + ":" + title;
        boolean dedupGuarded = false;
        try {
            if (!redisComponent.setIfAbsent(dedupKey, "1", 24, TimeUnit.HOURS)) {
                return;
            }
            dedupGuarded = true;
        } catch (Exception e) {
            log.warn("通知去重缓存不可用，将依赖数据库写入继续发送, userId={}, title={}", userId, title, e);
        }
        UserNotification notification = new UserNotification();
        notification.setNotificationId(StringTools.createNotificationId());
        notification.setUserId(userId);
        notification.setTitle(title);
        notification.setContent(content);
        notification.setBizType(bizType);
        notification.setBizId(bizId);
        notification.setReadStatus(0);
        notification.setCreateTime(new Date());
        try {
            userNotificationMapper.insert(notification);
        } catch (RuntimeException e) {
            if (dedupGuarded) {
                deleteDedupKey(dedupKey);
            }
            throw e;
        }
        if (dedupGuarded) {
            releaseDedupAfterRollback(dedupKey);
        }
        invalidateUnreadAfterCommit(userId);
        runAfterCommit(() -> notifyPushPublisher.push(notification));
    }

    @Override
    public void sendAsync(String userId, String title, String content, String bizType, String bizId) {
        if (StringTools.isEmpty(userId) || StringTools.isEmpty(title)) {
            return;
        }
        NotificationMessageDTO message = new NotificationMessageDTO(userId, title, content, bizType, bizId);
        transactionalMqSender.sendAfterCommit(
                RabbitMQConfig.NOTIFY_EXCHANGE,
                RabbitMQConfig.NOTIFY_KEY,
                message,
                MqIdempotencyKeys.notification(userId, bizType, bizId),
                MessageReliabilityLevelEnum.HIGH);
        log.info("通知已通过RabbitMQ发送等待异步落库, userId={}, title={}", userId, title);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void batchInsert(List<UserNotification> notifications) {
        if (notifications == null || notifications.isEmpty()) {
            return;
        }
        List<UserNotification> toInsert = new ArrayList<>();
        for (UserNotification notification : notifications) {
            if (isDuplicateBizNotification(notification)) {
                log.info("跳过重复通知 userId={}, bizType={}, bizId={}",
                        notification.getUserId(), notification.getBizType(), notification.getBizId());
                continue;
            }
            toInsert.add(notification);
        }
        if (toInsert.isEmpty()) {
            return;
        }
        userNotificationMapper.insertBatch(toInsert);
        toInsert.stream()
                .map(UserNotification::getUserId)
                .filter(userId -> !StringTools.isEmpty(userId))
                .distinct()
                .forEach(this::invalidateUnreadAfterCommit);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public boolean insertIfAbsent(UserNotification notification) {
        if (notification == null || StringTools.isEmpty(notification.getNotificationId())) {
            throw new IllegalArgumentException("通知及 notificationId 不能为空");
        }
        boolean inserted = userNotificationMapper.insertIgnore(notification) > 0;
        if (!inserted) {
            return false;
        }
        invalidateUnreadAfterCommit(notification.getUserId());
        return true;
    }

    private void runAfterCommit(Runnable action) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    action.run();
                }
            });
            return;
        }
        action.run();
    }

    private void releaseDedupAfterRollback(String dedupKey) {
        if (!TransactionSynchronizationManager.isSynchronizationActive()) {
            return;
        }
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCompletion(int status) {
                if (status != TransactionSynchronization.STATUS_COMMITTED) {
                    deleteDedupKey(dedupKey);
                }
            }
        });
    }

    private void deleteDedupKey(String dedupKey) {
        try {
            stringRedisTemplate.delete(dedupKey);
        } catch (Exception e) {
            log.warn("通知事务回滚后释放去重键失败, key={}", dedupKey, e);
        }
    }

    private boolean isDuplicateBizNotification(UserNotification notification) {
        if (StringTools.isEmpty(notification.getUserId())
                || StringTools.isEmpty(notification.getBizType())
                || StringTools.isEmpty(notification.getBizId())) {
            return false;
        }
        UserNotificationQuery query = new UserNotificationQuery();
        query.setUserId(notification.getUserId());
        query.setBizType(notification.getBizType());
        query.setBizId(notification.getBizId());
        return userNotificationMapper.selectCount(query) > 0;
    }

    @Override
    public UserNotification getPopupNotification(String userId) {
        if (StringTools.isEmpty(userId)) {
            return null;
        }
        // 从数据库中获取最新的未读通知
        UserNotificationQuery query = new UserNotificationQuery();
        query.setUserId(userId);
        query.setReadStatus(0);
        query.setOrderBy(com.aishop.entity.query.SafeSort.of("create_time desc"));
        query.setPageNo(1);
        SimplePage page = new SimplePage(1, 1, 1);
        query.setSimplePage(page);
        List<UserNotification> list = userNotificationMapper.selectList(query);

        if (list == null || list.isEmpty()) {
            return null;
        }

        // 检查Redis中是否有未弹窗标记
        for (UserNotification notification : list) {
            String popupKey = Constants.REDIS_KEY_USER_POPUP_NOTIFY + userId + ":" + notification.getNotificationId();
            if (Boolean.TRUE.equals(stringRedisTemplate.hasKey(popupKey))) {
                return notification;
            }
        }

        return null;
    }

    @Override
    public void clearPopupNotification(String userId, String notificationId) {
        if (StringTools.isEmpty(userId) || StringTools.isEmpty(notificationId)) {
            return;
        }
        String popupKey = Constants.REDIS_KEY_USER_POPUP_NOTIFY + userId + ":" + notificationId;
        redisUtils.delete(popupKey);
        log.info("清除未弹窗通知标记, userId={}, notificationId={}", userId, notificationId);
    }
}
