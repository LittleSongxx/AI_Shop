package com.aishop.controller.internal;

import com.aishop.controller.ABaseController;
import com.aishop.entity.po.DiscountCoupon;
import com.aishop.entity.po.UserCoupon;
import com.aishop.entity.query.DiscountCouponQuery;
import com.aishop.entity.query.UserCouponQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.mappers.DiscountCouponMapper;
import com.aishop.mappers.UserCouponMapper;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Date;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/internal/coupon/agent")
public class CouponAgentInternalController extends ABaseController {

    @Resource
    private UserCouponMapper<UserCoupon, UserCouponQuery> userCouponMapper;
    @Resource
    private DiscountCouponMapper<DiscountCoupon, DiscountCouponQuery> discountCouponMapper;
    @Resource
    private JdbcTemplate jdbcTemplate;

    @PostMapping("/listUserCoupons")
    public ResponseVO<List<Map<String, Object>>> listUserCoupons(@RequestBody Map<String, Object> body) {
        String userId = body == null || body.get("userId") == null ? null : String.valueOf(body.get("userId"));
        if (StringTools.isEmpty(userId)) {
            return getSuccessResponseVO(Collections.emptyList());
        }
        UserCouponQuery q = new UserCouponQuery();
        q.setUserId(userId);
        List<UserCoupon> list = userCouponMapper.selectList(q);
        List<Map<String, Object>> result = new ArrayList<>();
        if (list == null) {
            return getSuccessResponseVO(result);
        }
        for (UserCoupon uc : list) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("userCouponId", uc.getUserCouponId());
            m.put("userId", uc.getUserId());
            m.put("couponId", uc.getCouponId());
            m.put("status", uc.getStatus());
            DiscountCoupon dc = discountCouponMapper.selectByCouponId(uc.getCouponId());
            if (dc != null) {
                m.put("couponName", dc.getCouponName());
                m.put("couponType", dc.getCouponType());
                m.put("discountAmount", dc.getDiscountAmount());
                m.put("minAmount", dc.getThresholdAmount());
                m.put("thresholdAmount", dc.getThresholdAmount());
                m.put("validEndTime", dc.getValidEndTime());
            }
            result.add(m);
        }
        return getSuccessResponseVO(result);
    }

    /**
     * Estimate the best currently owned coupon for one unit of one exact SKU.
     * The method is read-only and intentionally does not lock or consume a
     * coupon. Cart/checkout remains the final transactional authority.
     */
    @PostMapping("/estimateSingleSkuOffers")
    public ResponseVO<List<Map<String, Object>>> estimateSingleSkuOffers(@RequestBody Map<String, Object> body) {
        String userId = body == null || body.get("userId") == null ? null : String.valueOf(body.get("userId"));
        Object rawItems = body == null ? null : body.get("items");
        if (StringTools.isEmpty(userId) || !(rawItems instanceof List<?> items) || items.isEmpty()) {
            return getSuccessResponseVO(Collections.emptyList());
        }
        UserCouponQuery query = new UserCouponQuery();
        query.setUserId(userId);
        query.setStatus(0);
        List<UserCoupon> userCoupons = userCouponMapper.selectList(query);
        Map<String, DiscountCoupon> coupons = new LinkedHashMap<>();
        for (UserCoupon userCoupon : userCoupons == null ? Collections.<UserCoupon>emptyList() : userCoupons) {
            if (userCoupon == null || StringTools.isEmpty(userCoupon.getCouponId())) {
                continue;
            }
            DiscountCoupon coupon = discountCouponMapper.selectByCouponId(userCoupon.getCouponId());
            if (coupon != null && usable(coupon, new Date())) {
                coupons.put(userCoupon.getUserCouponId(), coupon);
            }
        }
        List<Map<String, Object>> result = new ArrayList<>();
        for (Object raw : items) {
            if (!(raw instanceof Map<?, ?> item)) {
                continue;
            }
            String productId = stringValue(item.get("productId"));
            String categoryId = stringValue(item.get("categoryId"));
            String skuKey = stringValue(item.get("skuKey"));
            BigDecimal basePrice = decimalValue(item.get("basePrice"));
            if (StringTools.isEmpty(productId) || StringTools.isEmpty(skuKey) || basePrice == null || basePrice.signum() < 0) {
                continue;
            }
            Map<String, Object> best = null;
            BigDecimal bestDiscount = BigDecimal.ZERO;
            for (Map.Entry<String, DiscountCoupon> entry : coupons.entrySet()) {
                DiscountCoupon coupon = entry.getValue();
                if (!scopeMatches(coupon.getCouponId(), categoryId, productId, skuKey)) {
                    continue;
                }
                BigDecimal discount = discountFor(coupon, basePrice);
                if (discount == null || discount.compareTo(bestDiscount) <= 0) {
                    continue;
                }
                bestDiscount = discount;
                best = new LinkedHashMap<>();
                best.put("userCouponId", entry.getKey());
                best.put("couponName", coupon.getCouponName());
                best.put("estimatedDiscount", discount);
                best.put("estimatedPayable", basePrice.subtract(discount).max(BigDecimal.ZERO));
                best.put("validEndTime", coupon.getValidEndTime());
            }
            Map<String, Object> response = new LinkedHashMap<>();
            response.put("offerKey", productId + ":" + skuKey);
            if (best == null) {
                response.put("status", "NO_COUPON");
                response.put("estimatedPayable", basePrice);
                response.put("estimatedDiscount", BigDecimal.ZERO);
            } else {
                response.put("status", "AVAILABLE");
                response.putAll(best);
            }
            result.add(response);
        }
        return getSuccessResponseVO(result);
    }

    private boolean usable(DiscountCoupon coupon, Date now) {
        if (coupon.getStatus() == null || coupon.getStatus() != 1) {
            return false;
        }
        if (coupon.getValidStartTime() != null && coupon.getValidStartTime().after(now)) {
            return false;
        }
        if (coupon.getValidEndTime() != null && !coupon.getValidEndTime().after(now)) {
            return false;
        }
        return coupon.isUnlimitedStock() || (coupon.getRemainCount() != null && coupon.getRemainCount() > 0);
    }

    private boolean scopeMatches(String couponId, String categoryId, String productId, String skuKey) {
        List<Map<String, Object>> scopes;
        try {
            scopes = jdbcTemplate.queryForList(
                    "SELECT scope_type, scope_value FROM coupon_scope WHERE coupon_id=?", couponId);
        } catch (Exception ignored) {
            // Deployments that have not applied the new migration remain
            // conservative: old coupons are treated as globally scoped.
            return true;
        }
        if (scopes == null || scopes.isEmpty()) {
            return true;
        }
        for (Map<String, Object> scope : scopes) {
            String type = stringValue(scope.get("scope_type")).toUpperCase();
            String value = stringValue(scope.get("scope_value"));
            if ("GLOBAL".equals(type)
                    || ("CATEGORY".equals(type) && value.equals(categoryId))
                    || ("PRODUCT".equals(type) && value.equals(productId))
                    || ("SKU".equals(type) && value.equals(skuKey))) {
                return true;
            }
        }
        return false;
    }

    private BigDecimal discountFor(DiscountCoupon coupon, BigDecimal amount) {
        Integer type = coupon.getCouponType();
        BigDecimal threshold = coupon.getThresholdAmount() == null ? BigDecimal.ZERO : coupon.getThresholdAmount();
        if (type == null || amount.compareTo(threshold) < 0) {
            return null;
        }
        if (type == 2 && coupon.getDiscountRate() != null) {
            return amount.subtract(amount.multiply(coupon.getDiscountRate()))
                    .setScale(2, RoundingMode.DOWN).max(BigDecimal.ZERO);
        }
        if ((type == 1 || type == 3) && coupon.getDiscountAmount() != null) {
            return coupon.getDiscountAmount().min(amount).max(BigDecimal.ZERO);
        }
        return null;
    }

    private static String stringValue(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static BigDecimal decimalValue(Object value) {
        if (value == null) {
            return null;
        }
        try {
            return new BigDecimal(String.valueOf(value));
        } catch (NumberFormatException ignored) {
            return null;
        }
    }
}
