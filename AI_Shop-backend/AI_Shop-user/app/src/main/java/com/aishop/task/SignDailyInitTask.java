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

/**
 * 零点为活跃用户预建当月签到位图。
 * <p>默认关闭，且不随 {@code app.common-scheduling.enabled} 一起打开：
 * {@code initTodayBitmapForActiveUsers} 会无 LIMIT 地把整张 {@code user_sign_record}
 * 读进内存（mapper 的 selectList 只在 simplePage 非空时才拼 limit），写进 Redis 的却是
 * 全 0 位图——而 {@code getBit} 读一个不存在的键本来就返回 0，这些键存不存在不改变任何读路径。
 * 也就是说当前实现的代价是一次全表扫描，收益是零。
 * <p>要启用请先把那个查询改成分页，再打开 {@code app.sign.daily-init-enabled}。
 */
@Component
@Slf4j
@ConditionalOnProperty(name = "app.common-scheduling.enabled", havingValue = "true")
@ConditionalOnProperty(name = "app.sign.daily-init-enabled", havingValue = "true")
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
