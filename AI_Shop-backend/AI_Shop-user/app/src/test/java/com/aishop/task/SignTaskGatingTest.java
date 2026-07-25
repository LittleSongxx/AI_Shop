package com.aishop.task;

import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;

import java.util.Arrays;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 两个签到任务的注册条件不一样，这个差异是有意的，必须锁住。
 * <p>{@code SignReconcileTask} 是签到写 Redis 失败后唯一的兜底，跟着服务的调度总开关走。
 * {@code SignDailyInitTask} 额外要一个自己的开关：它零点把整张 user_sign_record 无 LIMIT
 * 读进内存，写进去的却是全 0 位图（{@code getBit} 读不存在的键本来就返回 0），
 * 代价是全表扫描、收益是零。谁要是顺手把这个额外开关删了，零点就会多一次全表扫描。
 */
class SignTaskGatingTest {

    private static final String SCHEDULING_FLAG = "app.common-scheduling.enabled";
    private static final String DAILY_INIT_FLAG = "app.sign.daily-init-enabled";

    private static List<String> flagNames(Class<?> type) {
        ConditionalOnProperty[] all = type.getAnnotationsByType(ConditionalOnProperty.class);
        return Arrays.stream(all).flatMap(a -> Arrays.stream(a.name())).sorted().toList();
    }

    @Test
    void reconcileTaskFollowsTheServiceSchedulingFlagOnly() {
        assertEquals(List.of(SCHEDULING_FLAG), flagNames(SignReconcileTask.class));
    }

    @Test
    void dailyInitTaskNeedsItsOwnFlagOnTopOfSchedulingFlag() {
        // 少了这个开关，打开服务调度总开关就会连带打开零点全表扫描
        assertEquals(List.of(SCHEDULING_FLAG, DAILY_INIT_FLAG).stream().sorted().toList(),
                flagNames(SignDailyInitTask.class));
    }

    @Test
    void bothFlagsRequireExplicitTrue() {
        // havingValue="true" 且不能有 matchIfMissing：键缺失时必须是"不注册"
        for (Class<?> type : List.of(SignReconcileTask.class, SignDailyInitTask.class)) {
            ConditionalOnProperty[] all = type.getAnnotationsByType(ConditionalOnProperty.class);
            assertNotNull(all);
            assertTrue(all.length > 0, type.getSimpleName() + " 没有任何开关，会无条件注册");
            for (ConditionalOnProperty flag : all) {
                assertEquals("true", flag.havingValue(), type.getSimpleName());
                assertEquals(false, flag.matchIfMissing(),
                        type.getSimpleName() + " 的 " + Arrays.toString(flag.name())
                                + " 用了 matchIfMissing，缺键时会被当成开启");
            }
        }
    }
}
