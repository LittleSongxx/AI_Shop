package com.aishop.biz;

import com.aishop.api.dto.CouponValidateAndLockDTO;
import com.aishop.api.dto.UserCouponCreateDTO;
import com.aishop.api.dto.UserCouponStatusChangeDTO;
import com.aishop.api.vo.CouponBriefVO;
import com.aishop.api.vo.CouponGrantResultVO;
import com.aishop.api.vo.CouponLockResultVO;
import com.aishop.api.vo.DiscountCouponVO;
import com.aishop.api.vo.UserCouponVO;
import com.aishop.api.enums.CouponStatusEnum;
import com.aishop.api.enums.CouponTypeEnum;
import com.aishop.api.enums.UserCouponStatusEnum;
import com.aishop.entity.po.DiscountCoupon;
import com.aishop.entity.po.UserCoupon;
import com.aishop.entity.query.DiscountCouponQuery;
import com.aishop.entity.query.UserCouponQuery;
import com.aishop.exception.BusinessException;
import com.aishop.component.CouponRushStockService;
import com.aishop.component.DiscountCouponCacheComponent;
import com.aishop.mappers.DiscountCouponMapper;
import com.aishop.mappers.UserCouponMapper;
import com.aishop.biz.DiscountCouponService;
import com.aishop.utils.OrderPayAmountUtil;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Date;

@Service
@Slf4j
public class CouponInternalService {

    @Resource
    private UserCouponMapper<UserCoupon, UserCouponQuery> userCouponMapper;
    @Resource
    private DiscountCouponMapper<DiscountCoupon, DiscountCouponQuery> discountCouponMapper;
    @Resource
    private CouponRushStockService couponRushStockService;
    @Resource
    private DiscountCouponService discountCouponService;
    @Resource
    private DiscountCouponCacheComponent discountCouponCacheComponent;

    @Transactional(rollbackFor = Exception.class)
    public CouponLockResultVO validateAndLock(CouponValidateAndLockDTO dto) {
        Date now = new Date();
        UserCoupon userCoupon = userCouponMapper.selectByUserCouponId(dto.getUserCouponId());
        if (userCoupon == null || !dto.getUserId().equals(userCoupon.getUserId())) {
            throw new BusinessException("优惠券不存在");
        }
        if (!UserCouponStatusEnum.NOUSE.getStatus().equals(userCoupon.getStatus())) {
            throw new BusinessException("优惠券不可用");
        }
        DiscountCoupon coupon = discountCouponMapper.selectByCouponId(userCoupon.getCouponId());
        if (coupon == null) {
            throw new BusinessException("优惠券不存在");
        }
        if (coupon.getValidStartTime() != null && now.before(coupon.getValidStartTime())) {
            throw new BusinessException("优惠券未到使用时间");
        }
        if (coupon.getValidEndTime() != null && now.after(coupon.getValidEndTime())) {
            throw new BusinessException("优惠券已过期");
        }
        BigDecimal orderAmount = dto.getOrderAmount() == null ? BigDecimal.ZERO : dto.getOrderAmount();
        BigDecimal threshold = coupon.getThresholdAmount() == null ? BigDecimal.ZERO : coupon.getThresholdAmount();
        if (threshold.compareTo(BigDecimal.ZERO) > 0 && orderAmount.compareTo(threshold) < 0) {
            throw new BusinessException("未满足优惠券使用门槛");
        }
        BigDecimal discount = calcCouponDiscount(coupon, orderAmount);
        discount = OrderPayAmountUtil.capCouponDiscountForMinPay(orderAmount, discount);

        CouponLockResultVO result = new CouponLockResultVO();
        result.setCouponId(coupon.getCouponId());
        result.setUserCouponId(dto.getUserCouponId());
        result.setCouponName(coupon.getCouponName());
        result.setDiscountAmount(discount);
        result.setLocked(false);
        if (discount.compareTo(BigDecimal.ZERO) > 0) {
            UserCoupon lockBean = new UserCoupon();
            lockBean.setStatus(UserCouponStatusEnum.CANT.getStatus());
            UserCouponQuery lockQuery = new UserCouponQuery();
            lockQuery.setUserCouponId(dto.getUserCouponId());
            lockQuery.setUserId(dto.getUserId());
            lockQuery.setStatus(UserCouponStatusEnum.NOUSE.getStatus());
            int updated = userCouponMapper.updateByParam(lockBean, lockQuery);
            if (updated != 1) {
                throw new BusinessException("优惠券已被使用");
            }
            result.setLocked(true);
        }
        return result;
    }

    public DiscountCouponVO getCoupon(String couponId) {
        DiscountCoupon coupon = discountCouponMapper.selectByCouponId(couponId);
        if (coupon == null) {
            return null;
        }
        DiscountCouponVO vo = new DiscountCouponVO();
        BeanUtils.copyProperties(coupon, vo);
        return vo;
    }

    public CouponBriefVO getCouponBrief(String couponId) {
        DiscountCoupon coupon = discountCouponMapper.selectByCouponId(couponId);
        if (coupon == null) {
            return null;
        }
        CouponBriefVO vo = new CouponBriefVO();
        vo.setCouponId(coupon.getCouponId());
        vo.setCouponName(coupon.getCouponName());
        vo.setCouponType(coupon.getCouponType());
        return vo;
    }

    public UserCouponVO getUserCoupon(String userCouponId) {
        UserCoupon uc = userCouponMapper.selectByUserCouponId(userCouponId);
        if (uc == null) {
            return null;
        }
        UserCouponVO vo = new UserCouponVO();
        BeanUtils.copyProperties(uc, vo);
        return vo;
    }

    @Transactional(rollbackFor = Exception.class)
    public void changeUserCouponStatus(UserCouponStatusChangeDTO dto) {
        UserCoupon bean = new UserCoupon();
        bean.setStatus(dto.getToStatus());
        if (dto.getUseTime() != null) {
            bean.setUseTime(dto.getUseTime());
        }
        UserCouponQuery query = new UserCouponQuery();
        query.setUserCouponId(dto.getUserCouponId());
        query.setUserId(dto.getUserId());
        query.setStatus(dto.getFromStatus());
        int updated = userCouponMapper.updateByParam(bean, query);
        if (updated < 1) {
            throw new BusinessException("用户券状态更新失败");
        }
    }

    @Transactional(rollbackFor = Exception.class)
    public void createUserCoupon(UserCouponCreateDTO dto) {
        UserCoupon userCoupon = new UserCoupon();
        userCoupon.setUserCouponId(dto.getUserCouponId());
        userCoupon.setUserId(dto.getUserId());
        userCoupon.setCouponId(dto.getCouponId());
        userCoupon.setStatus(dto.getStatus());
        userCouponMapper.insert(userCoupon);
    }

    @Transactional(rollbackFor = Exception.class)
    public CouponGrantResultVO grantCoupon(UserCouponCreateDTO dto) {
        if (dto == null
                || StringTools.isEmpty(dto.getUserCouponId())
                || StringTools.isEmpty(dto.getUserId())
                || StringTools.isEmpty(dto.getCouponId())
                || !UserCouponStatusEnum.NOUSE.getStatus().equals(dto.getStatus())) {
            throw new BusinessException("发券参数不完整");
        }
        DiscountCoupon locked = discountCouponMapper.selectByCouponIdForUpdate(dto.getCouponId());
        if (locked == null) {
            throw new BusinessException("优惠券不存在");
        }
        UserCoupon existing = userCouponMapper.selectByUserCouponId(dto.getUserCouponId());
        if (existing != null) {
            if (!dto.getUserId().equals(existing.getUserId())
                    || !dto.getCouponId().equals(existing.getCouponId())) {
                throw new BusinessException("用户券业务键冲突");
            }
            return grantResult(true, false, dto.getUserCouponId(), locked.getCouponName());
        }
        assertGrantable(locked, new Date());
        Integer affected = discountCouponMapper.deductStock(dto.getCouponId());
        if (affected == null || affected != 1) {
            return grantResult(false, false, dto.getUserCouponId(), locked.getCouponName());
        }
        UserCoupon userCoupon = new UserCoupon();
        userCoupon.setUserCouponId(dto.getUserCouponId());
        userCoupon.setUserId(dto.getUserId());
        userCoupon.setCouponId(dto.getCouponId());
        userCoupon.setStatus(dto.getStatus());
        userCouponMapper.insertGranted(userCoupon);
        invalidateCacheAfterCommit(dto.getCouponId());
        return grantResult(true, true, dto.getUserCouponId(), locked.getCouponName());
    }

    @Transactional(rollbackFor = Exception.class)
    public int deductStock(String couponId) {
        if (StringTools.isEmpty(couponId)) {
            throw new BusinessException("优惠券ID为空");
        }
        DiscountCoupon locked = discountCouponMapper.selectByCouponIdForUpdate(couponId);
        if (locked == null) {
            throw new BusinessException("优惠券不存在");
        }
        Integer affected = discountCouponMapper.deductStock(couponId);
        return affected == null ? 0 : affected;
    }

    private CouponGrantResultVO grantResult(
            boolean granted,
            boolean newlyGranted,
            String userCouponId,
            String couponName) {
        CouponGrantResultVO result = new CouponGrantResultVO();
        result.setGranted(granted);
        result.setNewlyGranted(newlyGranted);
        result.setUserCouponId(userCouponId);
        result.setCouponName(couponName);
        return result;
    }

    private void assertGrantable(DiscountCoupon coupon, Date now) {
        if (!CouponStatusEnum.NORMAL.getStatus().equals(coupon.getStatus())) {
            throw new BusinessException("优惠券当前不可发放");
        }
        if (coupon.getValidStartTime() != null && now.before(coupon.getValidStartTime())) {
            throw new BusinessException("优惠券未到发放时间");
        }
        if (coupon.getValidEndTime() != null && now.after(coupon.getValidEndTime())) {
            throw new BusinessException("优惠券已过期");
        }
    }

    private void invalidateCacheAfterCommit(String couponId) {
        Runnable action = () -> {
            try {
                discountCouponCacheComponent.invalidateAfterWrite(couponId);
            } catch (Exception e) {
                log.warn("发券后优惠券缓存失效失败，数据库库存仍为权威来源 couponId={}", couponId, e);
            }
        };
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

    public void assertRushNotBlocked(String couponId) {
        couponRushStockService.assertRushNotBlocked(couponId);
    }

    public boolean hasAvailableRushStock(String couponId) {
        return couponRushStockService.hasAvailableStock(couponId);
    }

    public void syncRushStockFromDbIfRedisZero(String couponId) {
        couponRushStockService.syncFromDbIfRedisZero(couponId);
    }

    public void releaseRushRedisReserve(String couponId, String userId) {
        discountCouponService.releaseRushRedisReserve(couponId, userId);
    }

    public void releaseRushCouponReserve(String couponId, String userId) {
        discountCouponService.releaseRushCouponReserve(couponId, userId);
    }

    public void invalidateCouponCache(String couponId) {
        discountCouponCacheComponent.invalidateAfterWrite(couponId);
    }

    private BigDecimal calcCouponDiscount(DiscountCoupon coupon, BigDecimal amount) {
        if (coupon == null || amount == null || amount.compareTo(BigDecimal.ZERO) <= 0) {
            return BigDecimal.ZERO;
        }
        Integer type = coupon.getCouponType();
        BigDecimal discount = BigDecimal.ZERO;
        if (CouponTypeEnum.FULL.getStatus().equals(type) || CouponTypeEnum.NOTHRESHOLD.getStatus().equals(type)) {
            discount = coupon.getDiscountAmount() == null ? BigDecimal.ZERO : coupon.getDiscountAmount();
        } else if (CouponTypeEnum.DISCOUNT.getStatus().equals(type)) {
            BigDecimal rate = coupon.getDiscountRate();
            if (rate == null) {
                return BigDecimal.ZERO;
            }
            discount = amount.multiply(BigDecimal.ONE.subtract(rate));
        }
        if (discount.compareTo(BigDecimal.ZERO) < 0) {
            discount = BigDecimal.ZERO;
        }
        if (discount.compareTo(amount) > 0) {
            discount = amount;
        }
        return discount.setScale(2, RoundingMode.HALF_UP);
    }
}
