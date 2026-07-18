package com.myshop.biz;

import com.myshop.api.dto.CouponValidateAndLockDTO;
import com.myshop.api.dto.UserCouponCreateDTO;
import com.myshop.api.dto.UserCouponStatusChangeDTO;
import com.myshop.api.vo.CouponBriefVO;
import com.myshop.api.vo.CouponLockResultVO;
import com.myshop.api.vo.DiscountCouponVO;
import com.myshop.api.vo.UserCouponVO;
import com.myshop.entity.enums.CouponTypeEnum;
import com.myshop.entity.enums.UserCouponStatusEnum;
import com.myshop.entity.po.DiscountCoupon;
import com.myshop.entity.po.UserCoupon;
import com.myshop.entity.query.DiscountCouponQuery;
import com.myshop.entity.query.UserCouponQuery;
import com.myshop.exception.BusinessException;
import com.myshop.component.CouponRushStockService;
import com.myshop.component.DiscountCouponCacheComponent;
import com.myshop.mappers.DiscountCouponMapper;
import com.myshop.mappers.UserCouponMapper;
import com.myshop.biz.DiscountCouponService;
import com.myshop.utils.OrderPayAmountUtil;
import com.myshop.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.Date;

@Service
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
        return discount.setScale(2, BigDecimal.ROUND_HALF_UP);
    }
}
