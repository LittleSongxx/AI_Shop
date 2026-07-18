package com.myshop.api.fallback;

import com.myshop.api.OrderFeignClient;
import com.myshop.api.dto.CouponRushPayRequestDTO;
import com.myshop.api.dto.CouponRushPrepareRequestDTO;
import com.myshop.api.dto.OrderIdDTO;
import com.myshop.api.dto.OrderStatsRangeDTO;
import com.myshop.api.dto.UserIdDTO;
import com.myshop.api.support.FeignFallbackResponses;
import com.myshop.api.vo.OrderBriefVO;
import com.myshop.api.vo.OrderDailyStatsVO;
import com.myshop.api.vo.OrderRangeStatsVO;
import com.myshop.entity.dto.CouponRushPrepareDTO;
import com.myshop.entity.dto.PayInfoDTO;
import com.myshop.entity.dto.PayOrderNotifyDTO;
import com.myshop.entity.vo.ResponseVO;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cloud.openfeign.FallbackFactory;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
public class OrderFeignFallbackFactory implements FallbackFactory<OrderFeignClient> {
    private static final Logger log = LoggerFactory.getLogger(OrderFeignFallbackFactory.class);

    @Override
    public OrderFeignClient create(Throwable cause) {
        return new OrderFeignClient() {
            @Override
            public ResponseVO<OrderBriefVO> getOrder(OrderIdDTO dto) {
                log.error("OrderFeign getOrder fallback, orderId={}", dto == null ? null : dto.getOrderId(), cause);
                return FeignFallbackResponses.unavailable(log, "订单服务", cause);
            }

            @Override
            public ResponseVO<Boolean> cancelUnpaidForPayTimeout(OrderIdDTO dto) {
                log.error("OrderFeign cancelUnpaid fallback, orderId={}", dto == null ? null : dto.getOrderId(), cause);
                return FeignFallbackResponses.unavailable(log, "订单服务", cause);
            }

            @Override
            public ResponseVO<Boolean> confirmReceipt(OrderIdDTO dto) {
                log.error("OrderFeign confirmReceipt fallback, orderId={}", dto == null ? null : dto.getOrderId(), cause);
                return FeignFallbackResponses.unavailable(log, "订单服务", cause);
            }

            @Override
            public ResponseVO<Void> onConfirmed(OrderIdDTO dto) {
                log.error("OrderFeign onConfirmed fallback, orderId={}", dto == null ? null : dto.getOrderId(), cause);
                return FeignFallbackResponses.unavailable(log, "订单服务", cause);
            }

            @Override
            public ResponseVO<Void> paySuccess(PayOrderNotifyDTO dto) {
                log.error("OrderFeign paySuccess fallback, payOrderId={}", dto == null ? null : dto.getPayOrderId(), cause);
                return FeignFallbackResponses.unavailable(log, "订单服务", cause);
            }

            @Override
            public ResponseVO<CouponRushPrepareDTO> prepareCouponRush(CouponRushPrepareRequestDTO dto) {
                return FeignFallbackResponses.unavailable(log, "订单服务", cause);
            }

            @Override
            public ResponseVO<PayInfoDTO> postCouponRushOrder(CouponRushPayRequestDTO dto) {
                return FeignFallbackResponses.unavailable(log, "订单服务", cause);
            }

            @Override
            public ResponseVO<Void> syncPaidCouponRushUserCoupons(UserIdDTO dto) {
                return FeignFallbackResponses.unavailable(log, "订单服务", cause);
            }

            @Override
            public ResponseVO<Void> cancelOrder(OrderIdDTO dto) {
                return FeignFallbackResponses.unavailable(log, "订单服务", cause);
            }

            @Override
            public ResponseVO<OrderRangeStatsVO> aggregateRange(OrderStatsRangeDTO dto) {
                log.error("OrderFeign aggregateRange fallback", cause);
                return FeignFallbackResponses.unavailable(log, "订单服务", cause);
            }

            @Override
            public ResponseVO<List<OrderDailyStatsVO>> aggregateDaily(OrderStatsRangeDTO dto) {
                log.error("OrderFeign aggregateDaily fallback", cause);
                return FeignFallbackResponses.unavailable(log, "订单服务", cause);
            }

            @Override
            public ResponseVO<Void> addAllWaitPayToDelayQueue() {
                log.error("OrderFeign addAllWaitPayToDelayQueue fallback", cause);
                return FeignFallbackResponses.unavailable(log, "订单服务", cause);
            }
        };
    }
}
