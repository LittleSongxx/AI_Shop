package com.simlect.api;

import com.simlect.api.dto.CouponIdDTO;
import com.simlect.api.dto.CouponValidateAndLockDTO;
import com.simlect.api.dto.UserCouponCreateDTO;
import com.simlect.api.dto.UserCouponIdDTO;
import com.simlect.api.dto.UserCouponStatusChangeDTO;
import com.simlect.api.vo.CouponBriefVO;
import com.simlect.api.vo.CouponLockResultVO;
import com.simlect.api.vo.DiscountCouponVO;
import com.simlect.api.vo.StockChangeResultVO;
import com.simlect.api.vo.UserCouponVO;
import com.simlect.api.fallback.CouponFeignFallbackFactory;
import com.simlect.entity.vo.ResponseVO;
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
    ResponseVO<Void> assertRushNotBlocked(@RequestBody com.simlect.api.dto.CouponRushOpsDTO dto);

    @PostMapping("/rush/hasAvailableStock")
    ResponseVO<Boolean> hasAvailableRushStock(@RequestBody com.simlect.api.dto.CouponRushOpsDTO dto);

    @PostMapping("/rush/syncFromDbIfRedisZero")
    ResponseVO<Void> syncRushStockFromDbIfRedisZero(@RequestBody com.simlect.api.dto.CouponRushOpsDTO dto);

    @PostMapping("/rush/releaseRedisReserve")
    ResponseVO<Void> releaseRushRedisReserve(@RequestBody com.simlect.api.dto.CouponRushOpsDTO dto);

    @PostMapping("/rush/releaseCouponReserve")
    ResponseVO<Void> releaseRushCouponReserve(@RequestBody com.simlect.api.dto.CouponRushOpsDTO dto);

    @PostMapping("/rush/invalidateCache")
    ResponseVO<Void> invalidateCouponCache(@RequestBody com.simlect.api.dto.CouponRushOpsDTO dto);
}
