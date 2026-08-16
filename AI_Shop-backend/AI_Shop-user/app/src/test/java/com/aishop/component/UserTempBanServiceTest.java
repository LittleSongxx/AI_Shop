package com.aishop.component;

import com.aishop.api.dto.UserTempBanDTO;
import com.aishop.api.enums.UserStatusEnum;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.TransactionalMqSender;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.po.UserInfo;
import com.aishop.entity.query.UserInfoQuery;
import com.aishop.mappers.UserInfoMapper;
import com.aishop.support.MqIdempotencyKeys;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class UserTempBanServiceTest {

    @Mock
    private UserInfoMapper<UserInfo, UserInfoQuery> userInfoMapper;
    @Mock
    private RedisComponent redisComponent;
    @Mock
    private StringRedisTemplate stringRedisTemplate;
    @Mock
    private ValueOperations<String, String> valueOperations;
    @Mock
    private TransactionalMqSender transactionalMqSender;
    @InjectMocks
    private UserTempBanService service;

    @Test
    void banPersistsDatabaseStateAndUsesTransactionalOutbox() {
        when(userInfoMapper.applyTemporaryBan(eq("u1"), anyLong())).thenReturn(1);
        when(stringRedisTemplate.opsForValue()).thenReturn(valueOperations);

        long unbanAtMs = service.banUserHours("u1", 2);

        verify(userInfoMapper).applyTemporaryBan("u1", unbanAtMs);
        ArgumentCaptor<UserTempBanDTO> payload = ArgumentCaptor.forClass(UserTempBanDTO.class);
        verify(transactionalMqSender).sendAfterCommit(
                eq(RabbitMQConfig.USER_TEMP_BAN_EXCHANGE),
                eq(RabbitMQConfig.USER_TEMP_BAN_DELAY_KEY),
                payload.capture(),
                eq(MqIdempotencyKeys.tempBanUnban("u1", unbanAtMs)),
                eq(MessageReliabilityLevelEnum.STANDARD));
        assertEquals("u1", payload.getValue().getUserId());
        assertEquals(unbanAtMs, payload.getValue().getUnbanAtMs());
        verify(redisComponent).cleanAllToken("u1");
    }

    @Test
    void expiredMqMessageReleasesFromDatabaseWhenRedisMarkerIsGone() {
        long expiredAt = System.currentTimeMillis() - 1_000L;
        when(userInfoMapper.selectByUserId("u1")).thenReturn(tempBannedUser("u1", expiredAt));
        when(userInfoMapper.clearTemporaryBanIfDue(eq("u1"), eq(expiredAt), anyLong()))
                .thenReturn(1);

        assertTrue(service.tryAutoUnban(new UserTempBanDTO("u1", expiredAt)));

        verify(userInfoMapper).clearTemporaryBanIfDue(eq("u1"), eq(expiredAt), anyLong());
        verify(stringRedisTemplate, never()).opsForValue();
    }

    @Test
    void olderMqMessageCannotReleaseANewerTemporaryBan() {
        long newerExpiry = System.currentTimeMillis() + 60_000L;
        when(userInfoMapper.selectByUserId("u1"))
                .thenReturn(tempBannedUser("u1", newerExpiry));

        assertFalse(service.tryAutoUnban(
                new UserTempBanDTO("u1", newerExpiry - 60_000L)));

        verify(userInfoMapper, never()).clearTemporaryBanIfDue(
                eq("u1"), anyLong(), anyLong());
    }

    @Test
    void reconciliationReleasesExpiredRowsWithoutMqDelivery() {
        long expiredAt = System.currentTimeMillis() - 1_000L;
        when(userInfoMapper.selectExpiredTemporaryBanUserIds(anyLong(), eq(100)))
                .thenReturn(List.of("u1"));
        when(userInfoMapper.selectByUserId("u1")).thenReturn(tempBannedUser("u1", expiredAt));
        when(userInfoMapper.clearTemporaryBanIfDue(eq("u1"), eq(null), anyLong()))
                .thenReturn(1);

        assertEquals(1, service.reconcileExpiredBans(100));
    }

    private static UserInfo tempBannedUser(String userId, long unbanAtMs) {
        UserInfo user = new UserInfo();
        user.setUserId(userId);
        user.setStatus(UserStatusEnum.DISABLE.getStatus());
        user.setTempBanUntilMs(unbanAtMs);
        return user;
    }
}
