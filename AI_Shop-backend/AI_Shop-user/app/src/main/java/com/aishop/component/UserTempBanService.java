package com.aishop.component;

import com.aishop.api.dto.UserTempBanDTO;
import com.aishop.api.enums.UserStatusEnum;
import com.aishop.constants.Constants;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.TransactionalMqSender;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.po.UserInfo;
import com.aishop.entity.query.UserInfoQuery;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.UserInfoMapper;
import com.aishop.support.MqIdempotencyKeys;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;
import java.util.concurrent.TimeUnit;

@Slf4j
@Component
public class UserTempBanService {

    private static final long TEMP_BAN_CACHE_GRACE_MS = TimeUnit.DAYS.toMillis(1);

    @Resource
    private UserInfoMapper<UserInfo, UserInfoQuery> userInfoMapper;
    @Resource
    private RedisComponent redisComponent;
    @Resource
    private StringRedisTemplate stringRedisTemplate;
    @Resource
    private TransactionalMqSender transactionalMqSender;

    public static String formatUnbanTime(long unbanAtMs) {
        return new SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new Date(unbanAtMs));
    }

    public String buildTempBanMessage(long unbanAtMs) {
        return "账号因违规被临时封禁，解封时间：" + formatUnbanTime(unbanAtMs);
    }

    @Transactional(rollbackFor = Exception.class)
    public long banUserHours(String userId, int hours) {
        if (StringTools.isEmpty(userId) || hours <= 0 || hours > 720) {
            throw new IllegalArgumentException("临时封禁参数不合法");
        }
        long unbanAtMs = System.currentTimeMillis() + TimeUnit.HOURS.toMillis(hours);
        if (userInfoMapper.applyTemporaryBan(userId, unbanAtMs) != 1) {
            throw new BusinessException("用户不存在");
        }
        transactionalMqSender.sendAfterCommit(
                RabbitMQConfig.USER_TEMP_BAN_EXCHANGE,
                RabbitMQConfig.USER_TEMP_BAN_DELAY_KEY,
                new UserTempBanDTO(userId, unbanAtMs),
                MqIdempotencyKeys.tempBanUnban(userId, unbanAtMs),
                MessageReliabilityLevelEnum.STANDARD);
        runAfterCommit(() -> {
            cleanTokensBestEffort(userId);
            cacheTempBanBestEffort(userId, unbanAtMs);
        });
        log.info("用户 {} 临时封禁 {} 小时，解封时间 {}", userId, hours, formatUnbanTime(unbanAtMs));
        return unbanAtMs;
    }

    public Long getUnbanAtMs(String userId) {
        if (StringTools.isEmpty(userId)) {
            return null;
        }
        UserInfo user = userInfoMapper.selectByUserId(userId);
        Long databaseExpiry = user == null ? null : user.getTempBanUntilMs();
        if (databaseExpiry != null) {
            if (databaseExpiry <= System.currentTimeMillis()) {
                if (!releaseDueTempBan(userId, databaseExpiry, "访问时到期对账")) {
                    UserInfo refreshed = userInfoMapper.selectByUserId(userId);
                    Long refreshedExpiry = refreshed == null ? null : refreshed.getTempBanUntilMs();
                    if (refreshedExpiry != null && refreshedExpiry > System.currentTimeMillis()) {
                        return refreshedExpiry;
                    }
                }
                return null;
            }
            return databaseExpiry;
        }
        return migrateLegacyRedisMarker(user);
    }

    public boolean isTempBanned(String userId) {
        Long unbanAtMs = getUnbanAtMs(userId);
        return unbanAtMs != null && unbanAtMs > System.currentTimeMillis();
    }

    public boolean tryAutoUnban(UserTempBanDTO dto) {
        if (dto == null || StringTools.isEmpty(dto.getUserId())) {
            return false;
        }
        return releaseDueTempBan(dto.getUserId(), dto.getUnbanAtMs(), "MQ 自动解封");
    }

    @Transactional(rollbackFor = Exception.class)
    public boolean manualUnban(String userId) {
        if (StringTools.isEmpty(userId) || userInfoMapper.clearTemporaryBanManually(userId) != 1) {
            return false;
        }
        runAfterCommit(() -> deleteCacheBestEffort(userId));
        log.info("用户 {} 已由管理员手动解除临时封禁", userId);
        return true;
    }

    public int reconcileExpiredBans(int batchSize) {
        int limit = Math.max(1, Math.min(batchSize, 500));
        List<String> userIds = userInfoMapper.selectExpiredTemporaryBanUserIds(
                System.currentTimeMillis(), limit);
        int released = 0;
        for (String userId : userIds) {
            if (releaseDueTempBan(userId, null, "定时对账解封")) {
                released++;
            }
        }
        return released;
    }

    @Transactional(rollbackFor = Exception.class)
    public void clearTempBanMark(String userId) {
        if (StringTools.isEmpty(userId)) {
            return;
        }
        userInfoMapper.clearTemporaryBanMarker(userId);
        runAfterCommit(() -> deleteCacheBestEffort(userId));
    }

    @Transactional(rollbackFor = Exception.class)
    public void banUserPermanent(String userId) {
        if (StringTools.isEmpty(userId) || userInfoMapper.setPermanentBan(userId) != 1) {
            throw new BusinessException("用户不存在");
        }
        runAfterCommit(() -> {
            deleteCacheBestEffort(userId);
            cleanTokensBestEffort(userId);
        });
    }

    private boolean releaseDueTempBan(String userId, Long expectedUnbanAtMs, String scene) {
        UserInfo user = userInfoMapper.selectByUserId(userId);
        Long currentUnbanAtMs = user == null ? null : user.getTempBanUntilMs();
        if (currentUnbanAtMs == null) {
            log.info("用户 {} 无数据库临时封禁记录，跳过{}", userId, scene);
            return false;
        }
        if (expectedUnbanAtMs != null && !expectedUnbanAtMs.equals(currentUnbanAtMs)) {
            log.info("用户 {} 忽略过期解封消息（期望 {}，当前 {}）",
                    userId, expectedUnbanAtMs, currentUnbanAtMs);
            return false;
        }
        long now = System.currentTimeMillis();
        if (now < currentUnbanAtMs) {
            log.info("用户 {} 解封时间未到，跳过{}", userId, scene);
            return false;
        }
        Integer cleared = userInfoMapper.clearTemporaryBanIfDue(
                userId, expectedUnbanAtMs, now);
        if (cleared == null || cleared != 1) {
            return false;
        }
        runAfterCommit(() -> deleteCacheBestEffort(userId));
        log.info("用户 {} 已{}，解封时间 {}", userId, scene, formatUnbanTime(currentUnbanAtMs));
        return true;
    }

    private Long migrateLegacyRedisMarker(UserInfo user) {
        if (user == null || !UserStatusEnum.DISABLE.getStatus().equals(user.getStatus())) {
            return null;
        }
        String userId = user.getUserId();
        String value;
        try {
            value = stringRedisTemplate.opsForValue().get(redisKey(userId));
        } catch (Exception e) {
            log.warn("读取用户临时封禁缓存失败, userId={}", userId, e);
            return null;
        }
        if (StringTools.isEmpty(value)) {
            return null;
        }
        long unbanAtMs;
        try {
            unbanAtMs = Long.parseLong(value);
        } catch (NumberFormatException e) {
            deleteCacheBestEffort(userId);
            return null;
        }
        userInfoMapper.applyTemporaryBan(userId, unbanAtMs);
        if (unbanAtMs <= System.currentTimeMillis()) {
            releaseDueTempBan(userId, unbanAtMs, "旧缓存迁移到期解封");
            return null;
        }
        cacheTempBanBestEffort(userId, unbanAtMs);
        log.info("用户 {} 的旧 Redis 临时封禁标记已迁移到数据库", userId);
        return unbanAtMs;
    }

    private void cacheTempBanBestEffort(String userId, long unbanAtMs) {
        long ttlMs = Math.max(
                TimeUnit.MINUTES.toMillis(1),
                unbanAtMs - System.currentTimeMillis() + TEMP_BAN_CACHE_GRACE_MS);
        try {
            stringRedisTemplate.opsForValue().set(
                    redisKey(userId), String.valueOf(unbanAtMs), ttlMs, TimeUnit.MILLISECONDS);
        } catch (Exception e) {
            log.warn("写入临时封禁缓存失败，数据库记录仍会驱动解封, userId={}", userId, e);
        }
    }

    private void deleteCacheBestEffort(String userId) {
        try {
            stringRedisTemplate.delete(redisKey(userId));
        } catch (Exception e) {
            log.warn("清理临时封禁缓存失败，缓存会在宽限 TTL 后过期, userId={}", userId, e);
        }
    }

    private void cleanTokensBestEffort(String userId) {
        try {
            redisComponent.cleanAllToken(userId);
        } catch (Exception e) {
            log.warn("清理封禁用户 Token 失败, userId={}", userId, e);
        }
    }

    private String redisKey(String userId) {
        return Constants.REDIS_USER_TEMP_BAN + userId;
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
}
