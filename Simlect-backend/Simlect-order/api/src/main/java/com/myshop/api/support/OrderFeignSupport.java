package com.myshop.api.support;

import com.myshop.api.OrderFeignClient;
import com.myshop.api.dto.CouponRushPayRequestDTO;
import com.myshop.api.dto.CouponRushPrepareRequestDTO;
import com.myshop.api.dto.OrderIdDTO;
import com.myshop.api.dto.OrderStatsRangeDTO;
import com.myshop.api.dto.UserIdDTO;
import com.myshop.api.vo.OrderBriefVO;
import com.myshop.api.vo.OrderDailyStatsVO;
import com.myshop.api.vo.OrderRangeStatsVO;
import com.myshop.entity.dto.CouponRushPrepareDTO;
import com.myshop.entity.dto.PayInfoDTO;
import com.myshop.entity.dto.PayOrderNotifyDTO;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Component;

import java.util.Collections;
import java.util.List;

@Component
public class OrderFeignSupport {

    @Resource
    private OrderFeignClient orderFeignClient;
    @Resource
    private FeignResponseSupport feignResponseSupport;

    public OrderBriefVO getOrder(String orderId) {
        return feignResponseSupport.call(() -> orderFeignClient.getOrder(new OrderIdDTO(orderId)), "查询订单失败");
    }

    public void paySuccess(PayOrderNotifyDTO dto) {
        feignResponseSupport.run(() -> orderFeignClient.paySuccess(dto), "支付成功处理失败");
    }

    public CouponRushPrepareDTO prepareCouponRush(String userId, String couponId) {
        return feignResponseSupport.call(
                () -> orderFeignClient.prepareCouponRush(new CouponRushPrepareRequestDTO(userId, couponId)),
                "秒杀预占失败");
    }

    public PayInfoDTO postCouponRushOrder(String userId, String couponId, String payMethod) {
        return feignResponseSupport.call(
                () -> orderFeignClient.postCouponRushOrder(new CouponRushPayRequestDTO(userId, couponId, payMethod)),
                "秒杀下单支付失败");
    }

    public void syncPaidCouponRushUserCoupons(String userId) {
        feignResponseSupport.run(
                () -> orderFeignClient.syncPaidCouponRushUserCoupons(new UserIdDTO(userId)),
                "同步秒杀用户券失败");
    }

    public void cancelOrder(String orderId, String userId) {
        feignResponseSupport.run(
                () -> orderFeignClient.cancelOrder(new OrderIdDTO(orderId, userId)),
                "取消订单失败");
    }

    public OrderRangeStatsVO aggregateRange(String startTime, String endTime) {
        return feignResponseSupport.call(
                () -> orderFeignClient.aggregateRange(new OrderStatsRangeDTO(startTime, endTime)),
                "订单区间统计失败");
    }

    public List<OrderDailyStatsVO> aggregateDaily(String startTime, String endTime) {
        List<OrderDailyStatsVO> list = feignResponseSupport.call(
                () -> orderFeignClient.aggregateDaily(new OrderStatsRangeDTO(startTime, endTime)),
                "订单按日统计失败");
        return list == null ? Collections.emptyList() : list;
    }

    public void addAllWaitPayToDelayQueue() {
        feignResponseSupport.run(orderFeignClient::addAllWaitPayToDelayQueue, "待付款订单加入延时队列失败");
    }
}
