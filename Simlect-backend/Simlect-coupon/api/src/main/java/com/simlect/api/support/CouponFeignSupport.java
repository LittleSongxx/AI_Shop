package com.simlect.api.support;

import com.simlect.api.CouponFeignClient;
import com.simlect.api.dto.CouponIdDTO;
import com.simlect.api.dto.CouponRushOpsDTO;
import com.simlect.api.dto.CouponValidateAndLockDTO;
import com.simlect.api.dto.UserCouponCreateDTO;
import com.simlect.api.dto.UserCouponIdDTO;
import com.simlect.api.dto.UserCouponStatusChangeDTO;
import com.simlect.api.vo.CouponBriefVO;
import com.simlect.api.vo.CouponLockResultVO;
import com.simlect.api.vo.DiscountCouponVO;
import com.simlect.api.vo.StockChangeResultVO;
import com.simlect.api.vo.UserCouponVO;
import com.simlect.compensation.UserCouponStatusCompensatePort;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.Date;

@Component
public class CouponFeignSupport implements UserCouponStatusCompensatePort {

    @Resource
    private CouponFeignClient couponFeignClient;
    @Resource
    private FeignResponseSupport feignResponseSupport;

    public CouponLockResultVO validateAndLock(String userId, String userCouponId, BigDecimal orderAmount) {
        CouponValidateAndLockDTO dto = new CouponValidateAndLockDTO();
        dto.setUserId(userId);
        dto.setUserCouponId(userCouponId);
        dto.setOrderAmount(orderAmount);
        return feignResponseSupport.call(() -> couponFeignClient.validateAndLock(dto), "优惠券校验失败");
    }

    public DiscountCouponVO getCoupon(String couponId) {
        return feignResponseSupport.call(() -> couponFeignClient.getCoupon(new CouponIdDTO(couponId)), "查询优惠券失败");
    }

    public CouponBriefVO getCouponBrief(String couponId) {
        return feignResponseSupport.call(
                () -> couponFeignClient.getCouponBrief(new CouponIdDTO(couponId)), "查询优惠券失败");
    }

    public UserCouponVO getUserCoupon(String userCouponId) {
        return feignResponseSupport.call(
                () -> couponFeignClient.getUserCoupon(new UserCouponIdDTO(userCouponId)), "查询用户券失败");
    }

    @Override
    public void changeUserCouponStatus(String userCouponId, String userId, Integer fromStatus, Integer toStatus, Date useTime) {
        UserCouponStatusChangeDTO dto = new UserCouponStatusChangeDTO();
        dto.setUserCouponId(userCouponId);
        dto.setUserId(userId);
        dto.setFromStatus(fromStatus);
        dto.setToStatus(toStatus);
        dto.setUseTime(useTime);
        feignResponseSupport.run(() -> couponFeignClient.changeUserCouponStatus(dto), "更新用户券状态失败");
    }

    public void createUserCoupon(UserCouponCreateDTO dto) {
        feignResponseSupport.run(() -> couponFeignClient.createUserCoupon(dto), "创建用户券失败");
    }

    public int deductStock(String couponId) {
        StockChangeResultVO vo = feignResponseSupport.call(
                () -> couponFeignClient.deductStock(new CouponIdDTO(couponId)), "扣减券库存失败");
        return vo == null || vo.getAffectedRows() == null ? 0 : vo.getAffectedRows();
    }

    public void assertRushNotBlocked(String couponId) {
        feignResponseSupport.run(
                () -> couponFeignClient.assertRushNotBlocked(new CouponRushOpsDTO(couponId)),
                "秒杀库存校验失败");
    }

    public boolean hasAvailableRushStock(String couponId) {
        Boolean ok = feignResponseSupport.call(
                () -> couponFeignClient.hasAvailableRushStock(new CouponRushOpsDTO(couponId)),
                "查询秒杀库存失败");
        return Boolean.TRUE.equals(ok);
    }

    public void syncRushStockFromDbIfRedisZero(String couponId) {
        feignResponseSupport.run(
                () -> couponFeignClient.syncRushStockFromDbIfRedisZero(new CouponRushOpsDTO(couponId)),
                "同步秒杀库存失败");
    }

    public void releaseRushRedisReserve(String couponId, String userId) {
        feignResponseSupport.run(
                () -> couponFeignClient.releaseRushRedisReserve(new CouponRushOpsDTO(couponId, userId)),
                "回滚秒杀预占失败");
    }

    public void releaseRushCouponReserve(String couponId, String userId) {
        feignResponseSupport.run(
                () -> couponFeignClient.releaseRushCouponReserve(new CouponRushOpsDTO(couponId, userId)),
                "回滚秒杀券库存失败");
    }

    public void invalidateCouponCache(String couponId) {
        feignResponseSupport.run(
                () -> couponFeignClient.invalidateCouponCache(new CouponRushOpsDTO(couponId)),
                "刷新优惠券缓存失败");
    }
}
