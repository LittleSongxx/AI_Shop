package com.aishop.component;

import com.aishop.constants.Constants;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import org.springframework.data.redis.core.Cursor;
import org.springframework.data.redis.core.ScanOptions;
import org.springframework.data.redis.core.SetOperations;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.stream.IntStream;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * 参与者 SET 清理的编排逻辑：SSCAN 遍历 → 按批交给 Lua。
 * <p>这里不验 Lua 的判定（那要真 Redis，见 {@code MiddlewareIT}），只验三件在单测里就能锁住的事：
 * 遍历是分批的、游标一定关、脚本抛异常不会漏掉游标。前两条是性能与资源约束，
 * 第三条是这段代码唯一的 try-with-resources 语义。
 */
@ExtendWith(MockitoExtension.class)
class CouponRushSweepTest {

    private static final int BATCH_SIZE = 200;

    @Mock
    private StringRedisTemplate stringRedisTemplate;
    @Mock
    private SetOperations<String, String> setOperations;
    @InjectMocks
    private CouponRushRedisComponent component;

    /** 记录 close() 是否被调用；用假实现而不是 mock，close 的断言才是真的行为而非"被 stub 过" */
    private static final class FakeCursor implements Cursor<String> {
        private final Iterator<String> delegate;
        private boolean closed;

        private FakeCursor(List<String> members) {
            this.delegate = members.iterator();
        }

        @Override
        public boolean hasNext() {
            return delegate.hasNext();
        }

        @Override
        public String next() {
            return delegate.next();
        }

        @Override
        public void close() {
            closed = true;
        }

        @Override
        public CursorId getId() {
            return CursorId.initial();
        }

        @Override
        public long getCursorId() {
            return 0L;
        }

        @Override
        public boolean isClosed() {
            return closed;
        }

        @Override
        public long getPosition() {
            return 0L;
        }
    }

    private FakeCursor stubScan(List<String> members) {
        FakeCursor cursor = new FakeCursor(members);
        when(stringRedisTemplate.opsForSet()).thenReturn(setOperations);
        when(setOperations.scan(any(String.class), any(ScanOptions.class))).thenReturn(cursor);
        return cursor;
    }

    private void stubScriptReturns(Long removedPerCall) {
        when(stringRedisTemplate.execute(
                any(DefaultRedisScript.class), anyList(), any(Object[].class)))
                .thenReturn(removedPerCall);
    }

    private static List<String> users(int count) {
        return IntStream.range(0, count).mapToObj(i -> "u" + i).toList();
    }

    @Test
    void blankCouponIdNeverTouchesRedis() {
        // 空 couponId 会拼成 "mall:rushing:coupon:" 这个全局键，扫它等于扫了别的东西
        assertEquals(0L, component.sweepDanglingRushParticipants(null));
        assertEquals(0L, component.sweepDanglingRushParticipants(""));
        verifyNoInteractions(stringRedisTemplate);
    }

    @Test
    void scansTheParticipantSetOfThatCouponWithBoundedBatch() {
        stubScan(List.of());

        component.sweepDanglingRushParticipants("c1");

        ArgumentCaptor<String> key = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<ScanOptions> options = ArgumentCaptor.forClass(ScanOptions.class);
        verify(setOperations).scan(key.capture(), options.capture());
        assertEquals(Constants.REDIS_KEY_RUSHING_COUPON + "c1", key.getValue());
        assertEquals(Long.valueOf(BATCH_SIZE), options.getValue().getCount());
    }

    @Test
    void emptySetRunsNoScript() {
        stubScan(List.of());

        assertEquals(0L, component.sweepDanglingRushParticipants("c1"));
        verify(stringRedisTemplate, never()).execute(
                any(DefaultRedisScript.class), anyList(), any(Object[].class));
    }

    @Test
    void oneBatchWhenSetFitsInBatchSize() {
        stubScan(users(BATCH_SIZE));
        stubScriptReturns(7L);

        assertEquals(7L, component.sweepDanglingRushParticipants("c1"));
        // 恰好 200 时不该多一次空批：满批就地清空，之后 batch 为空
        verify(stringRedisTemplate, times(1)).execute(
                any(DefaultRedisScript.class), anyList(), any(Object[].class));
    }

    @Test
    void splitsIntoBatchesAndSumsRemovedCounts() {
        // 201 个成员 = 200 + 1，第二批是循环外的收尾批，最容易被写漏
        stubScan(users(BATCH_SIZE + 1));
        stubScriptReturns(3L);

        assertEquals(6L, component.sweepDanglingRushParticipants("c1"));
        verify(stringRedisTemplate, times(2)).execute(
                any(DefaultRedisScript.class), anyList(), any(Object[].class));
    }

    @Test
    void largeSetIsChunkedNotSentAtOnce() {
        stubScan(users(1000));
        stubScriptReturns(0L);

        component.sweepDanglingRushParticipants("c1");

        // 1000/200 = 5 批；若有人把批量拆分去掉，这里会变成 1 次
        verify(stringRedisTemplate, times(5)).execute(
                any(DefaultRedisScript.class), anyList(), any(Object[].class));
    }

    @Test
    void skipsBlankMembersWithoutBreakingBatching() {
        List<String> members = new ArrayList<>(users(3));
        members.add("");
        stubScan(members);
        stubScriptReturns(1L);

        assertEquals(1L, component.sweepDanglingRushParticipants("c1"));
        // 空成员传进 Lua 会去 exists 一个拼错的键，只能在 Java 侧先滤掉
        verify(stringRedisTemplate, times(1)).execute(
                any(DefaultRedisScript.class), anyList(), any(Object[].class));
    }

    @Test
    void nullScriptResultCountsAsZero() {
        stubScan(users(2));
        stubScriptReturns(null);

        // 连接异常等情况下 execute 返回 null，累加前不判空会 NPE 掉整轮对账
        assertEquals(0L, component.sweepDanglingRushParticipants("c1"));
    }

    @Test
    void cursorIsClosedAfterNormalSweep() {
        FakeCursor cursor = stubScan(users(2));
        stubScriptReturns(1L);

        component.sweepDanglingRushParticipants("c1");

        assertTrue(cursor.isClosed(), "SSCAN 游标未关闭，服务端游标会一直占着");
    }

    @Test
    @MockitoSettings(strictness = Strictness.LENIENT)
    void cursorIsClosedWhenScriptThrows() {
        FakeCursor cursor = stubScan(users(BATCH_SIZE + 1));
        when(stringRedisTemplate.execute(
                any(DefaultRedisScript.class), anyList(), any(Object[].class)))
                .thenThrow(new IllegalStateException("redis down"));

        // 满批时脚本在 while 循环内执行，异常从 try 块里抛出，游标必须由 try-with-resources 关掉
        assertThrows(IllegalStateException.class,
                () -> component.sweepDanglingRushParticipants("c1"));
        assertTrue(cursor.isClosed(), "脚本抛异常时游标泄漏");
    }

    @Test
    void argvIsCouponIdThenUserIdsAndKeysIsEmpty() {
        stubScan(users(2));
        stubScriptReturns(0L);

        component.sweepDanglingRushParticipants("c-42");

        ArgumentCaptor<Object[]> argv = ArgumentCaptor.forClass(Object[].class);
        // KEYS 为空是有意的：键名由脚本用 couponId/userId 自己拼，Java 侧不重复一遍拼法
        verify(stringRedisTemplate).execute(
                any(DefaultRedisScript.class), eq(List.of()), argv.capture());
        // 脚本按 ARGV[1]=couponId、ARGV[2..n]=userId 读参，顺序错了会去 exists 一个用 userId 当券号的键
        assertArrayEquals(new Object[]{"c-42", "u0", "u1"}, argv.getValue());
    }
}
