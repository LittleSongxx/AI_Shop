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
public class SignDailyInitTask {

    @Resource
    private SignCalendarCacheService signCalendarCacheService;
    @Resource
    private RedisComponent redisComponent;

    @Scheduled(cron = "0 0 0 * * ?")
    public void initTodaySignBitmap() {
        String lockKey = Constants.REDIS_KEY_SIGN_DAILY_INIT_LOCK;
        if (!redisComponent.setIfAbsent(lockKey, "1", 10, TimeUnit.MINUTES)) {
            return;
        }
        try {
            signCalendarCacheService.initTodayBitmapForActiveUsers();
        } catch (Exception e) {
            log.error("签到每日初始化任务失败", e);
        }
    }
}
