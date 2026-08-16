package com.aishop.biz.impl;

import com.aishop.api.dto.UserCouponCreateDTO;
import com.aishop.api.dto.OrderGrowthEventDTO;
import com.aishop.api.support.CouponFeignSupport;
import com.aishop.api.vo.CouponGrantResultVO;
import com.aishop.component.RedisComponent;
import com.aishop.entity.enums.ResponseCodeEnum;
import com.aishop.api.enums.UserCouponStatusEnum;
import com.aishop.entity.po.UserMemberProfile;
import com.aishop.entity.po.UserOrderGrowth;
import com.aishop.entity.vo.MemberCenterVO;
import com.aishop.api.vo.MemberLevelRewardVO;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.UserMemberProfileMapper;
import com.aishop.mappers.UserMemberLevelRewardClaimMapper;
import com.aishop.mappers.UserOrderGrowthMapper;
import com.aishop.biz.MemberLevelRewardConfigService;
import com.aishop.biz.UserMemberProfileService;
import com.aishop.biz.UserNotificationService;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.HashSet;
import java.util.Objects;
import java.util.Set;

@Service("userMemberProfileService")
@Slf4j
public class UserMemberProfileServiceImpl implements UserMemberProfileService {

    private static final int SILVER_GROWTH = 1000;
    private static final int GOLD_GROWTH = 5000;
    private static final int BONUS_GROWTH_SILVER = 20;
    private static final int BONUS_GROWTH_GOLD = 50;

    @Resource
    private MemberLevelRewardConfigService memberLevelRewardConfigService;
    @Resource
    private RedisComponent redisComponent;
    @Resource
    private UserNotificationService userNotificationService;
    @Resource
    private CouponFeignSupport couponFeignSupport;
    @Resource
    private UserMemberProfileMapper<UserMemberProfile, com.aishop.entity.query.UserMemberProfileQuery> userMemberProfileMapper;
    @Resource
    private UserOrderGrowthMapper userOrderGrowthMapper;
    @Resource
    private UserMemberLevelRewardClaimMapper userMemberLevelRewardClaimMapper;

    @Override
    public UserMemberProfile getOrInitProfile(String userId) {
        UserMemberProfile profile = userMemberProfileMapper.selectByUserId(userId);
        if (profile != null) {
            refreshLevel(profile);
            return profile;
        }
        profile = new UserMemberProfile();
        profile.setUserId(userId);
        profile.setGrowthValue(0);
        profile.setLevelCode(1);
        profile.setLevelName("普通会员");
        profile.setUpdateTime(new Date());
        userMemberProfileMapper.insert(profile);
        return profile;
    }

    @Override
    public MemberCenterVO getMemberCenter(String userId) {
        UserMemberProfile profile = getOrInitProfile(userId);
        Set<Integer> cachedClaims = redisComponent.getMemberLevelClaimed(userId);
        Set<Integer> claimed = new HashSet<>(
                cachedClaims == null ? Set.of() : cachedClaims);
        List<Integer> persistedClaims =
                userMemberLevelRewardClaimMapper.selectClaimedLevels(userId);
        if (persistedClaims != null) {
            claimed.addAll(persistedClaims);
        }
        int growth = profile.getGrowthValue() == null ? 0 : profile.getGrowthValue();
        int levelCode = profile.getLevelCode() == null ? 1 : profile.getLevelCode();

        List<MemberLevelRewardVO> rewards = new ArrayList<>();
        rewards.add(buildReward(1, "普通会员", 0, "入会礼遇", "注册即享基础会员权益", growth, levelCode, claimed));
        rewards.add(buildReward(2, "银卡会员", SILVER_GROWTH, "银卡升级礼", "专属优惠券 + " + BONUS_GROWTH_SILVER + " 成长值", growth, levelCode, claimed));
        rewards.add(buildReward(3, "金卡会员", GOLD_GROWTH, "金卡升级礼", "专属优惠券 + " + BONUS_GROWTH_GOLD + " 成长值", growth, levelCode, claimed));

        MemberCenterVO vo = new MemberCenterVO();
        vo.setProfile(profile);
        vo.setRewards(rewards);
        if (growth < SILVER_GROWTH) {
            vo.setNextLevelCode(2);
            vo.setNextLevelGrowth(SILVER_GROWTH);
            vo.setGrowthToNext(SILVER_GROWTH - growth);
        } else if (growth < GOLD_GROWTH) {
            vo.setNextLevelCode(3);
            vo.setNextLevelGrowth(GOLD_GROWTH);
            vo.setGrowthToNext(GOLD_GROWTH - growth);
        } else {
            vo.setNextLevelCode(null);
            vo.setNextLevelGrowth(null);
            vo.setGrowthToNext(0);
        }
        return vo;
    }

    private MemberLevelRewardVO buildReward(int code, String name, int threshold, String title, String desc,
                                            int growth, int levelCode, Set<Integer> claimed) {
        MemberLevelRewardVO item = new MemberLevelRewardVO();
        item.setLevelCode(code);
        item.setLevelName(name);
        item.setGrowthThreshold(threshold);
        item.setRewardTitle(title);
        item.setRewardDesc(desc);
        boolean unlocked = growth >= threshold && levelCode >= code;
        item.setUnlocked(unlocked);
        boolean isClaimed = claimed.contains(code);
        item.setClaimed(isClaimed);
        item.setClaimable(unlocked && code > 1 && !isClaimed);
        return item;
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void claimLevelReward(String userId, Integer levelCode) {
        if (levelCode == null || (levelCode != 2 && levelCode != 3)) {
            throw new BusinessException(ResponseCodeEnum.CODE_600);
        }
        UserMemberProfile profile = getOrInitProfile(userId);
        int growth = profile.getGrowthValue() == null ? 0 : profile.getGrowthValue();
        int threshold = levelCode == 2 ? SILVER_GROWTH : GOLD_GROWTH;
        if (growth < threshold) {
            throw new BusinessException("成长值未达标，暂无法领取");
        }
        List<Integer> persistedClaims =
                userMemberLevelRewardClaimMapper.selectClaimedLevels(userId);
        Set<Integer> cachedClaims = redisComponent.getMemberLevelClaimed(userId);
        if ((persistedClaims != null && persistedClaims.contains(levelCode))
                || (cachedClaims != null && cachedClaims.contains(levelCode))) {
            throw new BusinessException("该等级奖励已领取");
        }
        String couponId = memberLevelRewardConfigService.resolveLevelCouponId(levelCode);
        String levelName = levelCode == 2 ? "银卡会员" : "金卡会员";
        int bonusGrowth = levelCode == 2 ? BONUS_GROWTH_SILVER : BONUS_GROWTH_GOLD;
        CouponGrantResultVO couponGrant = tryGrantCoupon(userId, couponId, levelCode);
        String userCouponId = couponGrant == null ? null : couponGrant.getUserCouponId();
        int inserted = userMemberLevelRewardClaimMapper.insertIgnore(
                userId, levelCode, userCouponId, bonusGrowth);
        if (inserted != 1) {
            throw new BusinessException("该等级奖励已领取");
        }
        addGrowth(userId, bonusGrowth);
        runAfterCommit(() -> cacheClaimBestEffort(userId, levelCode));
        String content = "恭喜升级为「" + levelName + "」！已发放 " + bonusGrowth + " 成长值";
        if (couponGrant != null && !StringTools.isEmpty(couponGrant.getCouponName())) {
            content += "，优惠券「" + couponGrant.getCouponName() + "」已放入「我的优惠券」";
        }
        userNotificationService.sendAsync(userId, "会员升级礼", content, "member_level", String.valueOf(levelCode));
    }

    private CouponGrantResultVO tryGrantCoupon(String userId, String couponId, int levelCode) {
        if (StringTools.isEmpty(couponId)) {
            return null;
        }
        String userCouponId = StringTools.createStableUserCouponId(
                "member_level", userId, levelCode + ":" + couponId);
        UserCouponCreateDTO createDTO = new UserCouponCreateDTO();
        createDTO.setUserCouponId(userCouponId);
        createDTO.setUserId(userId);
        createDTO.setCouponId(couponId);
        createDTO.setStatus(UserCouponStatusEnum.NOUSE.getStatus());
        CouponGrantResultVO result = couponFeignSupport.grantCoupon(createDTO);
        if (result == null || !Boolean.TRUE.equals(result.getGranted())) {
            throw new BusinessException("升级礼券发放失败，请稍后重试");
        }
        return result;
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void addGrowthOnPay(String userId, BigDecimal payAmount) {
        if (payAmount == null || payAmount.compareTo(BigDecimal.ZERO) <= 0) {
            return;
        }
        addGrowth(userId, growthForPay(payAmount));
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public boolean applyOrderGrowth(OrderGrowthEventDTO event) {
        if (event == null
                || StringTools.isEmpty(event.getOrderId())
                || StringTools.isEmpty(event.getUserId())
                || event.getPayAmount() == null
                || event.getPayAmount().compareTo(BigDecimal.ZERO) <= 0) {
            throw new BusinessException("订单成长值事件参数不完整");
        }
        int points = growthForPay(event.getPayAmount());
        UserOrderGrowth ledger = new UserOrderGrowth();
        ledger.setOrderId(event.getOrderId());
        ledger.setUserId(event.getUserId());
        ledger.setPayAmount(event.getPayAmount());
        ledger.setGrowthValue(points);
        ledger.setCreateTime(new Date());
        try {
            userOrderGrowthMapper.insert(ledger);
        } catch (DuplicateKeyException duplicate) {
            UserOrderGrowth existing = userOrderGrowthMapper.selectByOrderId(event.getOrderId());
            if (sameOrderGrowth(existing, event, points)) {
                return false;
            }
            throw new BusinessException("订单成长值事件业务键冲突");
        }
        addGrowth(event.getUserId(), points);
        return true;
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void addGrowth(String userId, int points) {
        if (points <= 0) {
            return;
        }
        if (StringTools.isEmpty(userId)) {
            throw new BusinessException("用户ID为空");
        }
        Integer rows = userMemberProfileMapper.incrementGrowth(userId, points, new Date());
        if (rows == null || rows == 0) {
            throw new IllegalStateException("成长值更新失败");
        }
    }

    private static int growthForPay(BigDecimal payAmount) {
        return Math.max(1, payAmount.intValue() / 100);
    }

    private static boolean sameOrderGrowth(
            UserOrderGrowth existing, OrderGrowthEventDTO event, int points) {
        return existing != null
                && Objects.equals(existing.getOrderId(), event.getOrderId())
                && Objects.equals(existing.getUserId(), event.getUserId())
                && existing.getPayAmount() != null
                && existing.getPayAmount().compareTo(event.getPayAmount()) == 0
                && Objects.equals(existing.getGrowthValue(), points);
    }

    private void cacheClaimBestEffort(String userId, Integer levelCode) {
        try {
            redisComponent.addMemberLevelClaimed(userId, levelCode);
        } catch (Exception e) {
            log.warn("会员等级奖励领取缓存写入失败，数据库账本仍为权威来源 userId={}, levelCode={}",
                    userId, levelCode, e);
        }
    }

    private void runAfterCommit(Runnable action) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    action.run();
                }
            });
            return;
        }
        action.run();
    }

    private void refreshLevel(UserMemberProfile profile) {
        int growth = profile.getGrowthValue() == null ? 0 : profile.getGrowthValue();
        if (growth >= 5000) {
            profile.setLevelCode(3);
            profile.setLevelName("金卡会员");
        } else if (growth >= 1000) {
            profile.setLevelCode(2);
            profile.setLevelName("银卡会员");
        } else {
            profile.setLevelCode(1);
            profile.setLevelName("普通会员");
        }
    }
}
