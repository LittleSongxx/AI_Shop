package com.aishop.biz;

import com.aishop.api.dto.UserCouponCreateDTO;
import com.aishop.api.enums.CouponStatusEnum;
import com.aishop.api.vo.CouponGrantResultVO;
import com.aishop.component.CouponRushStockService;
import com.aishop.component.DiscountCouponCacheComponent;
import com.aishop.entity.po.DiscountCoupon;
import com.aishop.entity.po.UserCoupon;
import com.aishop.entity.query.DiscountCouponQuery;
import com.aishop.entity.query.UserCouponQuery;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.DiscountCouponMapper;
import com.aishop.mappers.UserCouponMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Date;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class CouponInternalServiceTest {

    @Mock
    private UserCouponMapper<UserCoupon, UserCouponQuery> userCouponMapper;
    @Mock
    private DiscountCouponMapper<DiscountCoupon, DiscountCouponQuery> discountCouponMapper;
    @Mock
    private CouponRushStockService couponRushStockService;
    @Mock
    private DiscountCouponService discountCouponService;
    @Mock
    private DiscountCouponCacheComponent discountCouponCacheComponent;
    @InjectMocks
    private CouponInternalService service;

    @Test
    void retryReturnsExistingGrantWithoutDeductingStockAgain() {
        when(discountCouponMapper.selectByCouponIdForUpdate("c1")).thenReturn(coupon());
        UserCoupon existing = new UserCoupon();
        existing.setUserCouponId("uc1");
        existing.setUserId("u1");
        existing.setCouponId("c1");
        when(userCouponMapper.selectByUserCouponId("uc1")).thenReturn(existing);

        CouponGrantResultVO result = service.grantCoupon(dto());

        assertTrue(result.getGranted());
        assertFalse(result.getNewlyGranted());
        verify(discountCouponMapper, never()).deductStock("c1");
    }

    @Test
    void newGrantDeductsAndCreatesInTheSameServiceTransaction() {
        when(discountCouponMapper.selectByCouponIdForUpdate("c1")).thenReturn(coupon());
        when(discountCouponMapper.deductStock("c1")).thenReturn(1);

        CouponGrantResultVO result = service.grantCoupon(dto());

        assertTrue(result.getGranted());
        assertTrue(result.getNewlyGranted());
        verify(userCouponMapper).insertGranted(any(UserCoupon.class));
        verify(discountCouponCacheComponent).invalidateAfterWrite("c1");
    }

    @Test
    void conflictingStableIdIsRejected() {
        when(discountCouponMapper.selectByCouponIdForUpdate("c1")).thenReturn(coupon());
        UserCoupon existing = new UserCoupon();
        existing.setUserCouponId("uc1");
        existing.setUserId("other");
        existing.setCouponId("c1");
        when(userCouponMapper.selectByUserCouponId("uc1")).thenReturn(existing);

        assertThrows(BusinessException.class, () -> service.grantCoupon(dto()));
    }

    @Test
    void newGrantRejectsInactiveCouponBeforeStockDeduction() {
        DiscountCoupon coupon = coupon();
        coupon.setStatus(CouponStatusEnum.STOP.getStatus());
        when(discountCouponMapper.selectByCouponIdForUpdate("c1")).thenReturn(coupon);

        assertThrows(BusinessException.class, () -> service.grantCoupon(dto()));

        verify(discountCouponMapper, never()).deductStock("c1");
        verify(userCouponMapper, never()).insertGranted(any(UserCoupon.class));
    }

    @Test
    void cacheFailureAfterGrantDoesNotTurnSuccessIntoFailure() {
        when(discountCouponMapper.selectByCouponIdForUpdate("c1")).thenReturn(coupon());
        when(discountCouponMapper.deductStock("c1")).thenReturn(1);
        org.mockito.Mockito.doThrow(new IllegalStateException("redis unavailable"))
                .when(discountCouponCacheComponent).invalidateAfterWrite("c1");

        CouponGrantResultVO result = assertDoesNotThrow(() -> service.grantCoupon(dto()));

        assertTrue(result.getGranted());
        assertTrue(result.getNewlyGranted());
    }

    private static DiscountCoupon coupon() {
        DiscountCoupon coupon = new DiscountCoupon();
        coupon.setCouponId("c1");
        coupon.setCouponName("测试券");
        coupon.setStatus(CouponStatusEnum.NORMAL.getStatus());
        coupon.setValidStartTime(new Date(System.currentTimeMillis() - 60_000L));
        coupon.setValidEndTime(new Date(System.currentTimeMillis() + 60_000L));
        return coupon;
    }

    private static UserCouponCreateDTO dto() {
        UserCouponCreateDTO dto = new UserCouponCreateDTO();
        dto.setUserCouponId("uc1");
        dto.setUserId("u1");
        dto.setCouponId("c1");
        dto.setStatus(0);
        return dto;
    }
}
