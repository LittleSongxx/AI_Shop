package com.aishop.task;

import com.aishop.component.RedisComponent;
import com.aishop.constants.Constants;
import com.aishop.biz.SignCalendarCacheService;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

@Component
@Slf4j
@ConditionalOnProperty(name = "app.common-scheduling.enabled", havingValue = "true")
public class SignReconcileTask {

    @Resource
    private SignCalendarCacheService signCalendarCacheService;
    @Resource
    private RedisComponent redisComponent;

    @Scheduled(cron = "0 5 * * * ?")
    public void reconcileRecentSignDetails() {
        String lockKey = Constants.REDIS_KEY_SIGN_RECONCILE_LOCK;
        if (!redisComponent.setIfAbsent(lockKey, "1", 50, TimeUnit.MINUTES)) {
            return;
        }
        try {
            signCalendarCacheService.reconcileRecentHour();
        } catch (Exception e) {
            log.error("签到 Redis 对账任务失败", e);
        }
    }
}
