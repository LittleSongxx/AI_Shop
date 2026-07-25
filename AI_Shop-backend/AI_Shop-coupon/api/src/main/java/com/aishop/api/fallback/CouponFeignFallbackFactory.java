package com.aishop.api.fallback;

import com.aishop.api.CouponFeignClient;
import com.aishop.api.dto.CouponIdDTO;
import com.aishop.api.dto.CouponRushOpsDTO;
import com.aishop.api.dto.CouponValidateAndLockDTO;
import com.aishop.api.dto.UserCouponCreateDTO;
import com.aishop.api.dto.UserCouponIdDTO;
import com.aishop.api.dto.UserCouponStatusChangeDTO;
import com.aishop.api.support.FeignFallbackResponses;
import com.aishop.api.vo.CouponBriefVO;
import com.aishop.api.vo.CouponLockResultVO;
import com.aishop.api.vo.DiscountCouponVO;
import com.aishop.api.vo.StockChangeResultVO;
import com.aishop.api.vo.UserCouponVO;
import com.aishop.entity.vo.ResponseVO;
import lombok.extern.slf4j.Slf4j;
import org.springframework.cloud.openfeign.FallbackFactory;
import org.springframework.stereotype.Component;

@Slf4j
@Component
public class CouponFeignFallbackFactory implements FallbackFactory<CouponFeignClient> {

    @Override
    public CouponFeignClient create(Throwable cause) {
        log.warn("Coupon Feign fallback: {}", cause == null ? "unknown" : cause.toString());
        return new CouponFeignClient() {
            @Override
            public ResponseVO<CouponLockResultVO> validateAndLock(CouponValidateAndLockDTO dto) {
                return FeignFallbackResponses.unavailable("优惠券服务");
            }

            @Override
            public ResponseVO<DiscountCouponVO> getCoupon(CouponIdDTO dto) {
                return FeignFallbackResponses.unavailable("优惠券服务");
            }

            @Override
            public ResponseVO<CouponBriefVO> getCouponBrief(CouponIdDTO dto) {
                return FeignFallbackResponses.unavailable("优惠券服务");
            }

            @Override
            public ResponseVO<UserCouponVO> getUserCoupon(UserCouponIdDTO dto) {
                return FeignFallbackResponses.unavailable("优惠券服务");
            }

            @Override
            public ResponseVO<Void> changeUserCouponStatus(UserCouponStatusChangeDTO dto) {
                return FeignFallbackResponses.unavailable("优惠券服务");
            }

            @Override
            public ResponseVO<Void> createUserCoupon(UserCouponCreateDTO dto) {
                return FeignFallbackResponses.unavailable("优惠券服务");
            }

            @Override
            public ResponseVO<StockChangeResultVO> deductStock(CouponIdDTO dto) {
                return FeignFallbackResponses.unavailable("优惠券服务");
            }

            @Override
            public ResponseVO<Void> assertRushNotBlocked(CouponRushOpsDTO dto) {
                return FeignFallbackResponses.unavailable("优惠券服务");
            }

            @Override
            public ResponseVO<Boolean> hasAvailableRushStock(CouponRushOpsDTO dto) {
                return FeignFallbackResponses.unavailable("优惠券服务");
            }

            @Override
            public ResponseVO<Void> syncRushStockFromDbIfRedisZero(CouponRushOpsDTO dto) {
                return FeignFallbackResponses.unavailable("优惠券服务");
            }

            @Override
            public ResponseVO<Void> releaseRushRedisReserve(CouponRushOpsDTO dto) {
                return FeignFallbackResponses.unavailable("优惠券服务");
            }

            @Override
            public ResponseVO<Void> releaseRushCouponReserve(CouponRushOpsDTO dto) {
                return FeignFallbackResponses.unavailable("优惠券服务");
            }

            @Override
            public ResponseVO<Void> invalidateCouponCache(CouponRushOpsDTO dto) {
                return FeignFallbackResponses.unavailable("优惠券服务");
            }
        };
    }
}
