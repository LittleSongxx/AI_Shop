package com.aishop.component;

import com.aishop.api.enums.RushingCouponStatusEnum;
import com.aishop.constants.Constants;
import com.aishop.entity.po.DiscountCoupon;
import com.aishop.entity.query.DiscountCouponQuery;
import com.aishop.mappers.DiscountCouponMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 定时对账一轮的编排：库存以 DB 为准改回来 + 参与者 SET 摘掉僵尸成员。
 * <p>这里没有 stub {@code reconcileOne}，走的是真实现——只有真实现跑过，"adjusted 计数"
 * 才是被对账逻辑驱动的，而不是被我自己写的 stub 驱动的。
 * <p>重点是失败隔离：对账本身是兜底手段，某张券数据异常时整轮不能停在半路，
 * 否则一张坏券会让它后面所有券永远等不到对账。
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class CouponRushReconcileTest {

    @Mock
    private StringRedisTemplate stringRedisTemplate;
    @Mock
    private ValueOperations<String, String> valueOperations;
    @Mock
    private CouponRushRedisComponent couponRushRedisComponent;
    @Mock
    private DiscountCouponMapper<DiscountCoupon, DiscountCouponQuery> discountCouponMapper;
    @InjectMocks
    private CouponRushStockService service;

    @BeforeEach
    void setUp() {
        when(stringRedisTemplate.opsForValue()).thenReturn(valueOperations);
    }

    private static DiscountCoupon coupon(String couponId, Integer remain, Integer total) {
        DiscountCoupon c = new DiscountCoupon();
        c.setCouponId(couponId);
        c.setRemainCount(remain);
        c.setTotalCount(total);
        c.setRushingstatus(RushingCouponStatusEnum.YES.getStatus());
        return c;
    }

    /** Redis 里当前的库存值；null 表示键不存在 */
    private void redisStockIs(String couponId, String raw) {
        when(valueOperations.get(Constants.REDIS_KEY_RUSHING_STOCK + couponId)).thenReturn(raw);
    }

    private void rushingCouponsAre(DiscountCoupon... coupons) {
        when(discountCouponMapper.selectList(any(DiscountCouponQuery.class)))
                .thenReturn(coupons == null ? null : new ArrayList<>(Arrays.asList(coupons)));
        if (coupons != null) {
            for (DiscountCoupon c : coupons) {
                if (c != null && c.getCouponId() != null) {
                    when(discountCouponMapper.selectByCouponId(c.getCouponId())).thenReturn(c);
                }
            }
        }
    }

    @Test
    void onlyRushingCouponsAreScanned() {
        rushingCouponsAre(coupon("c1", 5, 100));
        redisStockIs("c1", "5");

        service.reconcileAndSweepAllRushing();

        // 非抢购券没有 Redis 预占，扫它们纯属浪费；筛选条件必须留在查询里
        ArgumentCaptor<DiscountCouponQuery> query = ArgumentCaptor.forClass(DiscountCouponQuery.class);
        verify(discountCouponMapper).selectList(query.capture());
        assertEquals(RushingCouponStatusEnum.YES.getStatus(), query.getValue().getRushingstatus());
    }

    @Test
    void noRushingCouponsIsANoOp() {
        rushingCouponsAre();

        CouponRushStockService.CouponRushReconcileSummary summary = service.reconcileAndSweepAllRushing();

        assertEquals(0, summary.getScanned());
        assertEquals(0, summary.getAdjusted());
        assertEquals(0L, summary.getSweptMembers());
        assertEquals(0, summary.getFailed());
        verify(couponRushRedisComponent, never()).sweepDanglingRushParticipants(anyString());
    }

    @Test
    void nullListFromMapperDoesNotThrow() {
        // mapper 返回 null 而不是空集合在这套代码里是常见的，直接 for 会 NPE
        when(discountCouponMapper.selectList(any(DiscountCouponQuery.class))).thenReturn(null);

        CouponRushStockService.CouponRushReconcileSummary summary = service.reconcileAndSweepAllRushing();

        assertEquals(0, summary.getScanned());
    }

    @Test
    void redisMatchingDbCountsAsScannedButNotAdjusted() {
        rushingCouponsAre(coupon("c1", 5, 100));
        redisStockIs("c1", "5");
        when(couponRushRedisComponent.sweepDanglingRushParticipants("c1")).thenReturn(0L);

        CouponRushStockService.CouponRushReconcileSummary summary = service.reconcileAndSweepAllRushing();

        assertEquals(1, summary.getScanned());
        assertEquals(0, summary.getAdjusted());
        // 一致时不该回写：无谓的 set 会把并发预占刚扣掉的量覆盖回去
        verify(valueOperations, never()).set(eq(Constants.REDIS_KEY_RUSHING_STOCK + "c1"), anyString());
    }

    @Test
    void redisDivergingFromDbCountsAsAdjusted() {
        // Redis 3 / DB 5：预占扣了但补偿没跑到，虚低的这 2 张永远卖不出去
        rushingCouponsAre(coupon("c1", 5, 100));
        redisStockIs("c1", "3");

        CouponRushStockService.CouponRushReconcileSummary summary = service.reconcileAndSweepAllRushing();

        assertEquals(1, summary.getScanned());
        assertEquals(1, summary.getAdjusted());
        verify(valueOperations).set(Constants.REDIS_KEY_RUSHING_STOCK + "c1", "5");
    }

    @Test
    void missingRedisKeyCountsAsAdjusted() {
        rushingCouponsAre(coupon("c1", 5, 100));
        redisStockIs("c1", null);

        CouponRushStockService.CouponRushReconcileSummary summary = service.reconcileAndSweepAllRushing();

        assertEquals(1, summary.getAdjusted());
        verify(valueOperations).set(Constants.REDIS_KEY_RUSHING_STOCK + "c1", "5");
    }

    @Test
    void sweepRunsForEveryScannedCouponAndCountsSum() {
        rushingCouponsAre(coupon("c1", 5, 100), coupon("c2", 8, 100));
        redisStockIs("c1", "5");
        redisStockIs("c2", "8");
        when(couponRushRedisComponent.sweepDanglingRushParticipants("c1")).thenReturn(2L);
        when(couponRushRedisComponent.sweepDanglingRushParticipants("c2")).thenReturn(3L);

        CouponRushStockService.CouponRushReconcileSummary summary = service.reconcileAndSweepAllRushing();

        assertEquals(2, summary.getScanned());
        assertEquals(5L, summary.getSweptMembers());
        verify(couponRushRedisComponent).sweepDanglingRushParticipants("c1");
        verify(couponRushRedisComponent).sweepDanglingRushParticipants("c2");
    }

    @Test
    void sweepStillRunsWhenStockWasAlreadyCorrect() {
        // 库存对得上不代表 SET 是干净的：预占过期只影响 hash，SET 成员照样残留
        rushingCouponsAre(coupon("c1", 5, 100));
        redisStockIs("c1", "5");
        when(couponRushRedisComponent.sweepDanglingRushParticipants("c1")).thenReturn(4L);

        CouponRushStockService.CouponRushReconcileSummary summary = service.reconcileAndSweepAllRushing();

        assertEquals(0, summary.getAdjusted());
        assertEquals(4L, summary.getSweptMembers());
    }

    @Test
    void oneFailingCouponDoesNotStopTheRound() {
        rushingCouponsAre(coupon("c1", 5, 100), coupon("bad", 5, 100), coupon("c3", 5, 100));
        redisStockIs("c1", "5");
        redisStockIs("c3", "5");
        redisStockIs("bad", "5");
        when(couponRushRedisComponent.sweepDanglingRushParticipants("c1")).thenReturn(1L);
        when(couponRushRedisComponent.sweepDanglingRushParticipants("bad"))
                .thenThrow(new IllegalStateException("redis down"));
        when(couponRushRedisComponent.sweepDanglingRushParticipants("c3")).thenReturn(2L);

        CouponRushStockService.CouponRushReconcileSummary summary = service.reconcileAndSweepAllRushing();

        assertEquals(3, summary.getScanned());
        assertEquals(1, summary.getFailed());
        // 关键断言：坏券之后的 c3 仍然被处理
        assertEquals(3L, summary.getSweptMembers());
        verify(couponRushRedisComponent).sweepDanglingRushParticipants("c3");
    }

    @Test
    void failureInReconcileAlsoLeavesTheRoundRunning() {
        // 失败点在 reconcileOne（查库抛异常）而不是 sweep，同样不该中断
        rushingCouponsAre(coupon("bad", 5, 100), coupon("c2", 5, 100));
        when(discountCouponMapper.selectByCouponId("bad")).thenThrow(new IllegalStateException("db down"));
        redisStockIs("c2", "5");
        when(couponRushRedisComponent.sweepDanglingRushParticipants("c2")).thenReturn(1L);

        CouponRushStockService.CouponRushReconcileSummary summary = service.reconcileAndSweepAllRushing();

        assertEquals(2, summary.getScanned());
        assertEquals(1, summary.getFailed());
        assertEquals(1L, summary.getSweptMembers());
        // 对账失败的券不该再去扫它的 SET：异常已经说明这张券的数据不可信
        verify(couponRushRedisComponent, never()).sweepDanglingRushParticipants("bad");
    }

    @Test
    void blankAndNullEntriesAreSkippedWithoutCounting() {
        List<DiscountCoupon> list = new ArrayList<>();
        list.add(null);
        list.add(coupon("", 5, 100));
        list.add(coupon("c1", 5, 100));
        when(discountCouponMapper.selectList(any(DiscountCouponQuery.class))).thenReturn(list);
        when(discountCouponMapper.selectByCouponId("c1")).thenReturn(coupon("c1", 5, 100));
        redisStockIs("c1", "5");

        CouponRushStockService.CouponRushReconcileSummary summary = service.reconcileAndSweepAllRushing();

        // 空 couponId 拼出来的键是 "mall:rushing:coupon:"，扫它会碰到不属于任何券的数据
        assertEquals(1, summary.getScanned());
        assertEquals(0, summary.getFailed());
        verify(couponRushRedisComponent, never()).sweepDanglingRushParticipants("");
    }

    @Test
    void unlimitedStockCouponIsAlignedToSentinelNotToRemainCount() {
        // totalCount=0 表示不限量，库存哨兵值是 -1；按 remainCount 对账会把不限量券改成有限量
        rushingCouponsAre(coupon("c1", 0, 0));
        redisStockIs("c1", "0");

        CouponRushStockService.CouponRushReconcileSummary summary = service.reconcileAndSweepAllRushing();

        assertEquals(1, summary.getAdjusted());
        verify(valueOperations).set(Constants.REDIS_KEY_RUSHING_STOCK + "c1",
                String.valueOf(Constants.RUSHING_STOCK_UNLIMITED));
    }
}
