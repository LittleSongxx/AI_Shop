package com.myshop.controller.internal;

import com.myshop.api.dto.CouponIdDTO;
import com.myshop.api.dto.CouponRushOpsDTO;
import com.myshop.api.dto.CouponValidateAndLockDTO;
import com.myshop.api.dto.UserCouponCreateDTO;
import com.myshop.api.dto.UserCouponIdDTO;
import com.myshop.api.dto.UserCouponStatusChangeDTO;
import com.myshop.api.vo.CouponBriefVO;
import com.myshop.api.vo.CouponLockResultVO;
import com.myshop.api.vo.DiscountCouponVO;
import com.myshop.api.vo.StockChangeResultVO;
import com.myshop.api.vo.UserCouponVO;
import com.myshop.biz.CouponInternalService;
import com.myshop.controller.ABaseController;
import com.myshop.entity.vo.ResponseVO;
import jakarta.annotation.Resource;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/internal/coupon")
public class CouponInternalController extends ABaseController {

    @Resource
    private CouponInternalService couponInternalService;

    @PostMapping("/validateAndLock")
    public ResponseVO<CouponLockResultVO> validateAndLock(@Valid @RequestBody CouponValidateAndLockDTO dto) {
        return getSuccessResponseVO(couponInternalService.validateAndLock(dto));
    }

    @PostMapping("/getCoupon")
    public ResponseVO<DiscountCouponVO> getCoupon(@Valid @RequestBody CouponIdDTO dto) {
        return getSuccessResponseVO(couponInternalService.getCoupon(dto.getCouponId()));
    }

    @PostMapping("/getCouponBrief")
    public ResponseVO<CouponBriefVO> getCouponBrief(@Valid @RequestBody CouponIdDTO dto) {
        return getSuccessResponseVO(couponInternalService.getCouponBrief(dto.getCouponId()));
    }

    @PostMapping("/getUserCoupon")
    public ResponseVO<UserCouponVO> getUserCoupon(@Valid @RequestBody UserCouponIdDTO dto) {
        return getSuccessResponseVO(couponInternalService.getUserCoupon(dto.getUserCouponId()));
    }

    @PostMapping("/changeUserCouponStatus")
    public ResponseVO<Void> changeUserCouponStatus(@Valid @RequestBody UserCouponStatusChangeDTO dto) {
        couponInternalService.changeUserCouponStatus(dto);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/createUserCoupon")
    public ResponseVO<Void> createUserCoupon(@Valid @RequestBody UserCouponCreateDTO dto) {
        couponInternalService.createUserCoupon(dto);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/deductStock")
    public ResponseVO<StockChangeResultVO> deductStock(@Valid @RequestBody CouponIdDTO dto) {
        return getSuccessResponseVO(new StockChangeResultVO(couponInternalService.deductStock(dto.getCouponId())));
    }

    @PostMapping("/rush/assertNotBlocked")
    public ResponseVO<Void> assertRushNotBlocked(@RequestBody CouponRushOpsDTO dto) {
        couponInternalService.assertRushNotBlocked(dto.getCouponId());
        return getSuccessResponseVO(null);
    }

    @PostMapping("/rush/hasAvailableStock")
    public ResponseVO<Boolean> hasAvailableRushStock(@RequestBody CouponRushOpsDTO dto) {
        return getSuccessResponseVO(couponInternalService.hasAvailableRushStock(dto.getCouponId()));
    }

    @PostMapping("/rush/syncFromDbIfRedisZero")
    public ResponseVO<Void> syncRushStockFromDbIfRedisZero(@RequestBody CouponRushOpsDTO dto) {
        couponInternalService.syncRushStockFromDbIfRedisZero(dto.getCouponId());
        return getSuccessResponseVO(null);
    }

    @PostMapping("/rush/releaseRedisReserve")
    public ResponseVO<Void> releaseRushRedisReserve(@RequestBody CouponRushOpsDTO dto) {
        couponInternalService.releaseRushRedisReserve(dto.getCouponId(), dto.getUserId());
        return getSuccessResponseVO(null);
    }

    @PostMapping("/rush/releaseCouponReserve")
    public ResponseVO<Void> releaseRushCouponReserve(@RequestBody CouponRushOpsDTO dto) {
        couponInternalService.releaseRushCouponReserve(dto.getCouponId(), dto.getUserId());
        return getSuccessResponseVO(null);
    }

    @PostMapping("/rush/invalidateCache")
    public ResponseVO<Void> invalidateCouponCache(@RequestBody CouponRushOpsDTO dto) {
        couponInternalService.invalidateCouponCache(dto.getCouponId());
        return getSuccessResponseVO(null);
    }
}
