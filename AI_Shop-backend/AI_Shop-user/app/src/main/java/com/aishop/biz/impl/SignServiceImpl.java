package com.aishop.biz.impl;

import com.aishop.component.SignRedisComponent;
import com.aishop.entity.dto.SignRewardConfigDTO;
import com.aishop.api.dto.SignRecordMessageDTO;
import com.aishop.entity.enums.ResponseCodeEnum;
import com.aishop.api.dto.UserCouponCreateDTO;
import com.aishop.api.support.CouponFeignSupport;
import com.aishop.api.vo.CouponGrantResultVO;
import com.aishop.api.enums.UserCouponStatusEnum;
import com.aishop.utils.StringTools;
import com.aishop.api.vo.SignDataVO;
import com.aishop.exception.BusinessException;
import com.aishop.biz.SignCalendarCacheService;
import com.aishop.biz.SignEventPersistenceService;
import com.aishop.biz.SignRewardConfigService;
import com.aishop.biz.SignRecordSyncService;
import com.aishop.biz.SignService;
import com.aishop.biz.UserNotificationService;
import com.aishop.utils.DateUtil;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.YearMonth;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;

@Service
public class SignServiceImpl implements SignService {

    @Resource
    private SignRedisComponent signRedisComponent;
    @Resource
    private UserNotificationService userNotificationService;
    @Resource
    private SignRewardConfigService signRewardConfigService;
    @Resource
    private CouponFeignSupport couponFeignSupport;
    @Resource
    private SignRecordSyncService signRecordSyncService;
    @Resource
    private SignCalendarCacheService signCalendarCacheService;
    @Resource
    private SignEventPersistenceService signEventPersistenceService;

    private static final int SIGN_GROWTH = 5;

    @Override
    public SignDataVO getSignCalendar(String userId, String yyyyMM) {
        signRecordSyncService.ensureHashHydrated(userId);
        signCalendarCacheService.ensureCalendarHydrated(userId);
        SignDataVO signDataVO = new SignDataVO();
        if (yyyyMM == null || yyyyMM.length() != 6) {
            throw new BusinessException(ResponseCodeEnum.CODE_600);
        }
        Integer continuousDays = signRedisComponent.getContinuousDays(userId);
        Integer remainSignCount = signRedisComponent.getRemainSignCount(userId);
        List<String> signDateList = new ArrayList<>();
        int days = getDayofMonth(yyyyMM);
        for (int day = 1; day <= days; day++) {
            if (signRedisComponent.isSign(userId, yyyyMM, day)) {
                String date = yyyyMM + String.format("%02d", day);
                signDateList.add(date);
            }
        }
        signDataVO.setContinuousDays(continuousDays);
        signDataVO.setSupplementCount(remainSignCount);
        signDataVO.setTotalSignDays(signRedisComponent.totalSignDays(userId));
        signDataVO.setSignDays(signDateList);
        return signDataVO;
    }

    @Override
    public void sign(String userId) {
        signRecordSyncService.ensureHashHydrated(userId);
        String yyyyMM = DateUtil.getTimeOnParttern(0, "yyyyMM");
        LocalDateTime now = LocalDateTime.now();
        int dayOfMonth = now.getDayOfMonth();
        boolean alreadySigned = signRedisComponent.isSign(userId, yyyyMM, dayOfMonth);
        if (!alreadySigned) {
            try {
                signRedisComponent.sign(userId);
            } catch (BusinessException duplicate) {
                if (!signRedisComponent.isSign(userId, yyyyMM, dayOfMonth)) {
                    throw duplicate;
                }
                alreadySigned = true;
            }
        }
        int continuousDays = signRedisComponent.getContinuousDays(userId);
        int totalSignDays = signRedisComponent.totalSignDays(userId);
        int usedCount = signRedisComponent.getUsedCount(userId);
        SignRecordMessageDTO event = new SignRecordMessageDTO(
                userId, continuousDays, totalSignDays, usedCount,
                DateUtil.getTimeOnParttern(0, "yyyyMMdd"), 0);
        boolean persisted = signEventPersistenceService.persist(event, SIGN_GROWTH);
        if (alreadySigned && !persisted) {
            tryGrantStreakCoupon(userId, continuousDays);
            throw new BusinessException("今天已经签到过了哦~");
        }
        tryGrantStreakCoupon(userId, continuousDays);
    }

    @Override
    public void msign(String userId, String yyyyMMdd) {
        signRecordSyncService.ensureHashHydrated(userId);
        LocalDate supplementDate;
        try {
            supplementDate = LocalDate.parse(yyyyMMdd, DateTimeFormatter.BASIC_ISO_DATE);
        } catch (Exception e) {
            throw new BusinessException("日期格式错误");
        }
        if (!supplementDate.isBefore(LocalDate.now())) {
            throw new BusinessException("只能补签今天之前的日期");
        }
        String yyyyMM = supplementDate.format(DateTimeFormatter.ofPattern("yyyyMM"));
        int dayOfMonth = supplementDate.getDayOfMonth();
        boolean alreadySigned = signRedisComponent.isSign(userId, yyyyMM, dayOfMonth);
        if (!alreadySigned) {
            if (signRedisComponent.getRemainSignCount(userId) <= 0) {
                throw new BusinessException("补签次数不足");
            }
            try {
                signRedisComponent.supplementSign(userId, yyyyMM, dayOfMonth);
            } catch (BusinessException duplicate) {
                if (!signRedisComponent.isSign(userId, yyyyMM, dayOfMonth)) {
                    throw duplicate;
                }
                alreadySigned = true;
            }
        }
        int continuousDays = signRedisComponent.getContinuousDays(userId);
        int totalSignDays = signRedisComponent.totalSignDays(userId);
        int usedCount = signRedisComponent.getUsedCount(userId);
        SignRecordMessageDTO event = new SignRecordMessageDTO(
                userId, continuousDays, totalSignDays, usedCount, yyyyMMdd, 1);
        boolean persisted = signEventPersistenceService.persist(event, SIGN_GROWTH);
        if (alreadySigned && !persisted) {
            tryGrantStreakCoupon(userId, continuousDays);
            throw new BusinessException("该日期已经签到过了哦~");
        }
        tryGrantStreakCoupon(userId, continuousDays);
        userNotificationService.sendAsync(userId, "补签成功",
                "补签已获得 " + SIGN_GROWTH + " 成长值", "sign", yyyyMMdd);
    }

    private void tryGrantStreakCoupon(String userId, int continuousDays) {
        SignRewardConfigDTO config = signRewardConfigService.resolveActiveConfig();
        if (config == null || !Boolean.TRUE.equals(config.getEnabled())) {
            return;
        }
        int streakDays = config.getStreakDays() == null ? 7 : config.getStreakDays();
        if (streakDays < 1 || continuousDays < streakDays || continuousDays % streakDays != 0) {
            return;
        }
        String couponId = config.getCouponId();
        if (StringTools.isEmpty(couponId)) {
            return;
        }
        String userCouponId = StringTools.createStableUserCouponId(
                "sign_reward", userId, continuousDays + ":" + couponId);
        UserCouponCreateDTO createDTO = new UserCouponCreateDTO();
        createDTO.setUserCouponId(userCouponId);
        createDTO.setUserId(userId);
        createDTO.setCouponId(couponId);
        createDTO.setStatus(UserCouponStatusEnum.NOUSE.getStatus());
        CouponGrantResultVO grant = couponFeignSupport.grantCoupon(createDTO);
        if (grant == null || !Boolean.TRUE.equals(grant.getGranted())) {
            return;
        }
        String couponName = StringTools.isEmpty(grant.getCouponName())
                ? "签到奖励券" : grant.getCouponName();
        userNotificationService.sendAsync(userId, "签到奖励",
                "连续签到 " + continuousDays + " 天，已发放优惠券「" + couponName + "」",
                "sign_reward", userCouponId);
    }

    private int getDayofMonth(String yyyyMM){
        int year = Integer.parseInt(yyyyMM.substring(0, 4));
        int month = Integer.parseInt(yyyyMM.substring(4, 6));
        YearMonth yearMonth = YearMonth.of(year, month);
        return yearMonth.lengthOfMonth();
    }
}
