package com.aishop.biz.impl;

import com.aishop.api.support.UserFeignSupport;
import com.aishop.mappers.UserCouponMapper;
import com.aishop.biz.CouponReminderService;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;

@Service("couponReminderService")
@Slf4j
public class CouponReminderServiceImpl implements CouponReminderService {

    private static final int BATCH_LIMIT = 500;

    @Resource
    private UserCouponMapper<com.aishop.entity.po.UserCoupon, com.aishop.entity.query.UserCouponQuery> userCouponMapper;
    @Resource
    private UserFeignSupport userFeignSupport;

    @Override
    public void remindExpiringCoupons() {
        List<Map<String, Object>> rows = userCouponMapper.selectExpiringUnused(BATCH_LIMIT);
        if (rows == null || rows.isEmpty()) {
            return;
        }
        int sent = 0;
        for (Map<String, Object> row : rows) {
            String userId = stringVal(row.get("userId"));
            String userCouponId = stringVal(row.get("userCouponId"));
            String couponName = stringVal(row.get("couponName"));
            if (StringTools.isEmpty(userId) || StringTools.isEmpty(userCouponId)) {
                continue;
            }
            String title = "优惠券即将过期";
            String content = "您的「" + (StringTools.isEmpty(couponName) ? "优惠券" : couponName)
                    + "」将在 3 天内过期，请尽快使用";
            userFeignSupport.sendNotifyAsync(userId, title, content, "coupon_expire", userCouponId);
            sent++;
        }
        log.info("优惠券即将过期提醒完成，处理 {} 条", sent);
    }

    private static String stringVal(Object v) {
        return v == null ? null : String.valueOf(v);
    }
}
