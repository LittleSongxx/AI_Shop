package com.aishop.task;

import com.aishop.component.CouponRushStockService;
import com.aishop.component.RedisComponent;
import com.aishop.constants.Constants;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

/**
 * 秒杀库存与 Redis 预占的定时对账。
 * <p>抢购路径上每一步失败都有对应的回滚（Lua 回滚、死信兜底、Seata 全局事务），
 * 但这些补偿本身也会失败：MQ 消息丢了、消费者在 TTL 内一直没起来、实例在
 * "Redis 已扣、DB 未扣"之间被 kill。这类残留没有任何一方会再回来处理，
 * 表现是库存虚少（明明有货却抢不到）和参与者 SET 只增不减。
 * <p>所以要有一个不依赖任何在途消息的周期性收敛：以 DB 为准把 Redis 改回来。
 * 已有的 {@code syncFromDbIfRedisZero} 只在"抢到 0 库存"时被动触发，
 * 库存虚少但没到 0 的情况它看不见。
 */
@Component
@Slf4j
@ConditionalOnProperty(name = "app.common-scheduling.enabled", havingValue = "true")
public class CouponRushReconcileTask {

    @Resource
    private CouponRushStockService couponRushStockService;
    @Resource
    private RedisComponent redisComponent;

    /**
     * 每 10 分钟一轮。锁只靠 TTL 释放（与 {@code SignReconcileTask} 一致）：TTL 取到略短于
     * 周期，跑完不主动删，下一轮照常能拿到。不主动删是有意的——主动删可能删掉的是
     * 本轮超时后另一个实例刚拿到的锁。
     */
    @Scheduled(cron = "0 */10 * * * ?")
    public void reconcileRushStock() {
        String lockKey = Constants.REDIS_KEY_COUPON_RUSH_RECONCILE_LOCK;
        if (!redisComponent.setIfAbsent(lockKey, "1", 9, TimeUnit.MINUTES)) {
            return;
        }
        try {
            couponRushStockService.reconcileAndSweepAllRushing();
        } catch (Exception e) {
            log.error("秒杀库存对账任务失败", e);
        }
    }
}
