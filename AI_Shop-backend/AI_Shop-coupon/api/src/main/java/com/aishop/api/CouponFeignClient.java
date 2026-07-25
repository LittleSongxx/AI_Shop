package com.aishop.api;

import com.aishop.api.dto.CouponIdDTO;
import com.aishop.api.dto.CouponValidateAndLockDTO;
import com.aishop.api.dto.UserCouponCreateDTO;
import com.aishop.api.dto.UserCouponIdDTO;
import com.aishop.api.dto.UserCouponStatusChangeDTO;
import com.aishop.api.vo.CouponBriefVO;
import com.aishop.api.vo.CouponLockResultVO;
import com.aishop.api.vo.DiscountCouponVO;
import com.aishop.api.vo.StockChangeResultVO;
import com.aishop.api.vo.UserCouponVO;
import com.aishop.api.fallback.CouponFeignFallbackFactory;
import com.aishop.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

@FeignClient(name = "aishop-coupon", contextId = "couponFeignClient", path = "/internal/coupon",
        fallbackFactory = CouponFeignFallbackFactory.class)
public interface CouponFeignClient {

    @PostMapping("/validateAndLock")
    ResponseVO<CouponLockResultVO> validateAndLock(@RequestBody CouponValidateAndLockDTO dto);

    @PostMapping("/getCoupon")
    ResponseVO<DiscountCouponVO> getCoupon(@RequestBody CouponIdDTO dto);

    @PostMapping("/getCouponBrief")
    ResponseVO<CouponBriefVO> getCouponBrief(@RequestBody CouponIdDTO dto);

    @PostMapping("/getUserCoupon")
    ResponseVO<UserCouponVO> getUserCoupon(@RequestBody UserCouponIdDTO dto);

    @PostMapping("/changeUserCouponStatus")
    ResponseVO<Void> changeUserCouponStatus(@RequestBody UserCouponStatusChangeDTO dto);

    @PostMapping("/createUserCoupon")
    ResponseVO<Void> createUserCoupon(@RequestBody UserCouponCreateDTO dto);

    @PostMapping("/deductStock")
    ResponseVO<StockChangeResultVO> deductStock(@RequestBody CouponIdDTO dto);

    @PostMapping("/rush/assertNotBlocked")
    ResponseVO<Void> assertRushNotBlocked(@RequestBody com.aishop.api.dto.CouponRushOpsDTO dto);

    @PostMapping("/rush/hasAvailableStock")
    ResponseVO<Boolean> hasAvailableRushStock(@RequestBody com.aishop.api.dto.CouponRushOpsDTO dto);

    @PostMapping("/rush/syncFromDbIfRedisZero")
    ResponseVO<Void> syncRushStockFromDbIfRedisZero(@RequestBody com.aishop.api.dto.CouponRushOpsDTO dto);

    @PostMapping("/rush/releaseRedisReserve")
    ResponseVO<Void> releaseRushRedisReserve(@RequestBody com.aishop.api.dto.CouponRushOpsDTO dto);

    @PostMapping("/rush/releaseCouponReserve")
    ResponseVO<Void> releaseRushCouponReserve(@RequestBody com.aishop.api.dto.CouponRushOpsDTO dto);

    @PostMapping("/rush/invalidateCache")
    ResponseVO<Void> invalidateCouponCache(@RequestBody com.aishop.api.dto.CouponRushOpsDTO dto);
}
