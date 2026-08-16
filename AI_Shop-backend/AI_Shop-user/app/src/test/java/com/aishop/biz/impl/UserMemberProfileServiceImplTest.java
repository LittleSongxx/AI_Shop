package com.aishop.biz.impl;

import com.aishop.api.dto.OrderGrowthEventDTO;
import com.aishop.api.support.CouponFeignSupport;
import com.aishop.api.vo.CouponGrantResultVO;
import com.aishop.biz.MemberLevelRewardConfigService;
import com.aishop.biz.UserNotificationService;
import com.aishop.component.RedisComponent;
import com.aishop.entity.po.UserMemberProfile;
import com.aishop.entity.po.UserOrderGrowth;
import com.aishop.entity.query.UserMemberProfileQuery;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.UserMemberProfileMapper;
import com.aishop.mappers.UserMemberLevelRewardClaimMapper;
import com.aishop.mappers.UserOrderGrowthMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.dao.DuplicateKeyException;

import java.math.BigDecimal;
import java.util.Date;
import java.util.List;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class UserMemberProfileServiceImplTest {

    @Mock
    private UserMemberProfileMapper<UserMemberProfile, UserMemberProfileQuery> profileMapper;
    @Mock
    private UserOrderGrowthMapper orderGrowthMapper;
    @Mock
    private UserMemberLevelRewardClaimMapper claimMapper;
    @Mock
    private RedisComponent redisComponent;
    @Mock
    private MemberLevelRewardConfigService rewardConfigService;
    @Mock
    private CouponFeignSupport couponFeignSupport;
    @Mock
    private UserNotificationService userNotificationService;
    @InjectMocks
    private UserMemberProfileServiceImpl service;

    @Test
    void firstOrderEventWritesLedgerThenIncrementsGrowth() {
        when(orderGrowthMapper.insert(any(UserOrderGrowth.class))).thenReturn(1);
        when(profileMapper.incrementGrowth(eq("user-1"), eq(2), any(Date.class)))
                .thenReturn(1);

        assertTrue(service.applyOrderGrowth(event("user-1", "268.00")));

        ArgumentCaptor<UserOrderGrowth> ledgerCaptor =
                ArgumentCaptor.forClass(UserOrderGrowth.class);
        verify(orderGrowthMapper).insert(ledgerCaptor.capture());
        assertEquals("order-1", ledgerCaptor.getValue().getOrderId());
        assertEquals(2, ledgerCaptor.getValue().getGrowthValue());
        verify(profileMapper).incrementGrowth(eq("user-1"), eq(2), any(Date.class));
    }

    @Test
    void exactRedeliveryIsAcknowledgedWithoutAddingGrowthTwice() {
        OrderGrowthEventDTO event = event("user-1", "268.00");
        when(orderGrowthMapper.insert(any(UserOrderGrowth.class)))
                .thenThrow(new DuplicateKeyException("duplicate"));
        when(orderGrowthMapper.selectByOrderId("order-1"))
                .thenReturn(existing("user-1", "268.00", 2));

        assertFalse(service.applyOrderGrowth(event));

        verify(profileMapper, never()).incrementGrowth(any(), any(Integer.class), any());
    }

    @Test
    void reusedOrderKeyWithDifferentPayloadIsRejected() {
        when(orderGrowthMapper.insert(any(UserOrderGrowth.class)))
                .thenThrow(new DuplicateKeyException("duplicate"));
        when(orderGrowthMapper.selectByOrderId("order-1"))
                .thenReturn(existing("another-user", "268.00", 2));

        assertThrows(
                BusinessException.class,
                () -> service.applyOrderGrowth(event("user-1", "268.00")));

        verify(profileMapper, never()).incrementGrowth(any(), any(Integer.class), any());
    }

    @Test
    void ordinaryGrowthUsesAtomicIncrementInsteadOfReadModifyWrite() {
        when(profileMapper.incrementGrowth(eq("user-1"), eq(20), any(Date.class)))
                .thenReturn(2);

        service.addGrowth("user-1", 20);

        verify(profileMapper).incrementGrowth(eq("user-1"), eq(20), any(Date.class));
        verify(profileMapper, never()).selectByUserId(any());
    }

    @Test
    void levelRewardUsesCouponGrantAndDatabaseClaimLedger() {
        UserMemberProfile profile = new UserMemberProfile();
        profile.setUserId("user-1");
        profile.setGrowthValue(1_000);
        profile.setLevelCode(2);
        when(profileMapper.selectByUserId("user-1")).thenReturn(profile);
        when(claimMapper.selectClaimedLevels("user-1")).thenReturn(List.of());
        when(redisComponent.getMemberLevelClaimed("user-1")).thenReturn(Set.of());
        when(rewardConfigService.resolveLevelCouponId(2)).thenReturn("coupon-1");
        CouponGrantResultVO grant = new CouponGrantResultVO();
        grant.setGranted(true);
        grant.setNewlyGranted(false);
        grant.setUserCouponId("stable-coupon-id");
        grant.setCouponName("银卡礼券");
        when(couponFeignSupport.grantCoupon(any())).thenReturn(grant);
        when(claimMapper.insertIgnore("user-1", 2, "stable-coupon-id", 20)).thenReturn(1);
        when(profileMapper.incrementGrowth(eq("user-1"), eq(20), any(Date.class)))
                .thenReturn(1);

        service.claimLevelReward("user-1", 2);

        verify(couponFeignSupport).grantCoupon(any());
        verify(claimMapper).insertIgnore("user-1", 2, "stable-coupon-id", 20);
        verify(profileMapper).incrementGrowth(eq("user-1"), eq(20), any(Date.class));
        verify(redisComponent).addMemberLevelClaimed("user-1", 2);
    }

    @Test
    void persistedLevelClaimBlocksRemoteGrantAndDuplicateGrowth() {
        UserMemberProfile profile = new UserMemberProfile();
        profile.setUserId("user-1");
        profile.setGrowthValue(1_000);
        profile.setLevelCode(2);
        when(profileMapper.selectByUserId("user-1")).thenReturn(profile);
        when(claimMapper.selectClaimedLevels("user-1")).thenReturn(List.of(2));

        assertThrows(BusinessException.class, () -> service.claimLevelReward("user-1", 2));

        verify(couponFeignSupport, never()).grantCoupon(any());
        verify(profileMapper, never()).incrementGrowth(any(), any(Integer.class), any());
    }

    @Test
    void redisClaimCacheFailureDoesNotTurnCommittedRewardIntoFailure() {
        UserMemberProfile profile = new UserMemberProfile();
        profile.setUserId("user-1");
        profile.setGrowthValue(1_000);
        profile.setLevelCode(2);
        when(profileMapper.selectByUserId("user-1")).thenReturn(profile);
        when(claimMapper.selectClaimedLevels("user-1")).thenReturn(List.of());
        when(redisComponent.getMemberLevelClaimed("user-1")).thenReturn(Set.of());
        when(rewardConfigService.resolveLevelCouponId(2)).thenReturn(null);
        when(claimMapper.insertIgnore("user-1", 2, null, 20)).thenReturn(1);
        when(profileMapper.incrementGrowth(eq("user-1"), eq(20), any(Date.class)))
                .thenReturn(1);
        doThrow(new IllegalStateException("redis unavailable"))
                .when(redisComponent).addMemberLevelClaimed("user-1", 2);

        assertDoesNotThrow(() -> service.claimLevelReward("user-1", 2));

        verify(claimMapper).insertIgnore("user-1", 2, null, 20);
        verify(profileMapper).incrementGrowth(eq("user-1"), eq(20), any(Date.class));
        verify(userNotificationService).sendAsync(
                eq("user-1"), eq("会员升级礼"), any(), eq("member_level"), eq("2"));
    }

    private static OrderGrowthEventDTO event(String userId, String amount) {
        return new OrderGrowthEventDTO("order-1", userId, new BigDecimal(amount));
    }

    private static UserOrderGrowth existing(String userId, String amount, int points) {
        UserOrderGrowth existing = new UserOrderGrowth();
        existing.setOrderId("order-1");
        existing.setUserId(userId);
        existing.setPayAmount(new BigDecimal(amount));
        existing.setGrowthValue(points);
        return existing;
    }
}
