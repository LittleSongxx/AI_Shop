package com.myshop.api;

import com.myshop.api.dto.CouponIdDTO;
import com.myshop.api.dto.CouponValidateAndLockDTO;
import com.myshop.api.dto.UserCouponCreateDTO;
import com.myshop.api.dto.UserCouponIdDTO;
import com.myshop.api.dto.UserCouponStatusChangeDTO;
import com.myshop.api.vo.CouponBriefVO;
import com.myshop.api.vo.CouponLockResultVO;
import com.myshop.api.vo.DiscountCouponVO;
import com.myshop.api.vo.StockChangeResultVO;
import com.myshop.api.vo.UserCouponVO;
import com.myshop.api.fallback.CouponFeignFallbackFactory;
import com.myshop.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

@FeignClient(name = "simlect-coupon", contextId = "couponFeignClient", path = "/internal/coupon",
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
    ResponseVO<Void> assertRushNotBlocked(@RequestBody com.myshop.api.dto.CouponRushOpsDTO dto);

    @PostMapping("/rush/hasAvailableStock")
    ResponseVO<Boolean> hasAvailableRushStock(@RequestBody com.myshop.api.dto.CouponRushOpsDTO dto);

    @PostMapping("/rush/syncFromDbIfRedisZero")
    ResponseVO<Void> syncRushStockFromDbIfRedisZero(@RequestBody com.myshop.api.dto.CouponRushOpsDTO dto);

    @PostMapping("/rush/releaseRedisReserve")
    ResponseVO<Void> releaseRushRedisReserve(@RequestBody com.myshop.api.dto.CouponRushOpsDTO dto);

    @PostMapping("/rush/releaseCouponReserve")
    ResponseVO<Void> releaseRushCouponReserve(@RequestBody com.myshop.api.dto.CouponRushOpsDTO dto);

    @PostMapping("/rush/invalidateCache")
    ResponseVO<Void> invalidateCouponCache(@RequestBody com.myshop.api.dto.CouponRushOpsDTO dto);
}
