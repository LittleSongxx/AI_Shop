package com.aishop.task;

import com.aishop.component.CouponRushStockService;
import com.aishop.component.RedisComponent;
import com.aishop.constants.Constants;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;

import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * 对账任务的两条约束：多实例下同一轮只跑一次，以及本轮失败不能把调度线程带走。
 */
@ExtendWith(MockitoExtension.class)
class CouponRushReconcileTaskTest {

    @Mock
    private CouponRushStockService couponRushStockService;
    @Mock
    private RedisComponent redisComponent;
    @InjectMocks
    private CouponRushReconcileTask task;

    private void lockAcquired(boolean acquired) {
        when(redisComponent.setIfAbsent(anyString(), anyString(), anyLong(), any(TimeUnit.class)))
                .thenReturn(acquired);
    }

    @Test
    void runsReconcileWhenLockAcquired() {
        lockAcquired(true);

        task.reconcileRushStock();

        verify(couponRushStockService).reconcileAndSweepAllRushing();
    }

    @Test
    void skipsWhenAnotherInstanceHoldsTheLock() {
        lockAcquired(false);

        task.reconcileRushStock();

        // 多实例同时跑会互相覆盖 Redis 库存值：A 读到 DB=5 要写回时，B 刚被预占扣成 4
        verifyNoInteractions(couponRushStockService);
    }

    @Test
    void lockTtlIsShorterThanTheScheduleInterval() {
        lockAcquired(true);

        task.reconcileRushStock();

        // 锁不主动删，只靠 TTL 过期。TTL 若 >= 调度周期，下一轮永远拿不到锁，对账静默停摆
        verify(redisComponent).setIfAbsent(
                eq(Constants.REDIS_KEY_COUPON_RUSH_RECONCILE_LOCK),
                anyString(), eq(9L), eq(TimeUnit.MINUTES));
    }

    @Test
    void failureInsideTheRoundDoesNotPropagate() {
        lockAcquired(true);
        when(couponRushStockService.reconcileAndSweepAllRushing())
                .thenThrow(new IllegalStateException("db down"));

        // 异常抛到 @Scheduled 外层不会重试，只会在日志里留一条；但如果任务方法抛出，
        // 同一个调度线程上的其它任务也会受影响，所以这里必须自己吞掉
        task.reconcileRushStock();

        verify(couponRushStockService).reconcileAndSweepAllRushing();
    }

    @Test
    void taskIsGatedBySchedulingFlagAndRunsEveryTenMinutes() throws Exception {
        // 开关与 cron 是部署契约的一部分：默认不注册（本服务多实例部署，且该表只在部分库里有），
        // 打开后按 10 分钟一轮。改这两处等于改运维行为，不该悄悄发生。
        ConditionalOnProperty flag = CouponRushReconcileTask.class
                .getAnnotation(ConditionalOnProperty.class);
        assertNotNull(flag, "缺少开关注解，任务会在所有环境无条件注册");
        assertEquals("app.common-scheduling.enabled", flag.name()[0]);
        assertEquals("true", flag.havingValue());

        Scheduled scheduled = CouponRushReconcileTask.class
                .getMethod("reconcileRushStock").getAnnotation(Scheduled.class);
        assertNotNull(scheduled);
        assertEquals("0 */10 * * * ?", scheduled.cron());
    }
}
