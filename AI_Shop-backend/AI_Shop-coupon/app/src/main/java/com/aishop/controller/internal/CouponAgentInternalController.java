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
}
