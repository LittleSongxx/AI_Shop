package com.aishop.component;

import com.aishop.constants.Constants;
import com.aishop.exception.BusinessException;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Service;

import java.util.Collections;

@Service
@Slf4j
public class CouponRushRateLimitService {

    private static final String RATE_LIMIT_LUA =
            "local current = redis.call('INCR', KEYS[1]); "
                    + "if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]); end "
                    + "if current > tonumber(ARGV[2]) then return 0 else return 1 end;";

    /**
     * 脚本实例复用：{@link DefaultRedisScript} 的 sha1 是加锁懒加载的，多线程共享一个实例安全。
     * 限流是每次抢购请求都要走的路径，每次新建实例等于每次重算 sha1、退回 EVAL 传全量脚本文本。
     */
    private static final DefaultRedisScript<Long> RATE_LIMIT_SCRIPT =
            new DefaultRedisScript<>(RATE_LIMIT_LUA, Long.class);

    @Resource
    private StringRedisTemplate stringRedisTemplate;

    public void checkUserLimit(String userId) {
        checkUserLimit(userId, Constants.RUSH_RATE_USER_MAX_PER_MINUTE, Constants.RUSH_RATE_USER_WINDOW_SECONDS);
    }

    public void checkCouponLimit(String couponId) {
        checkCouponLimit(couponId, Constants.RUSH_RATE_COUPON_MAX_PER_SECOND, Constants.RUSH_RATE_COUPON_WINDOW_SECONDS);
    }

    public void checkUserLimit(String userId, int maxCount, long windowSeconds) {
        if (StringTools.isEmpty(userId)) {
            throw new BusinessException("请先登录");
        }
        String key = Constants.REDIS_KEY_RUSH_RATE_USER + userId;
        if (!tryAcquire(key, windowSeconds, maxCount)) {
            throw new BusinessException("操作过于频繁，请稍后再试");
        }
    }

    public void checkCouponLimit(String couponId, int maxCount, long windowSeconds) {
        if (StringTools.isEmpty(couponId)) {
            return;
        }
        String key = Constants.REDIS_KEY_RUSH_RATE_COUPON + couponId;
        if (!tryAcquire(key, windowSeconds, maxCount)) {
            throw new BusinessException("当前抢购人数过多，请稍后再试");
        }
    }

    public boolean tryAcquire(String key, long windowSeconds, int maxCount) {
        return tryAcquireInternal(key, windowSeconds, maxCount);
    }

    private boolean tryAcquireInternal(String key, long windowSeconds, int maxCount) {
        Long ok = stringRedisTemplate.execute(
                RATE_LIMIT_SCRIPT,
                Collections.singletonList(key),
                String.valueOf(windowSeconds),
                String.valueOf(maxCount)
        );
        return ok != null && ok == 1L;
    }
}
