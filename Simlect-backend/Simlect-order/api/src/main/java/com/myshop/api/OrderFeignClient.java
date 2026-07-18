package com.myshop.api;

import com.myshop.api.dto.CouponRushPayRequestDTO;
import com.myshop.api.dto.CouponRushPrepareRequestDTO;
import com.myshop.api.dto.OrderIdDTO;
import com.myshop.api.dto.OrderStatsRangeDTO;
import com.myshop.api.dto.UserIdDTO;
import com.myshop.api.fallback.OrderFeignFallbackFactory;
import com.myshop.api.vo.OrderBriefVO;
import com.myshop.api.vo.OrderDailyStatsVO;
import com.myshop.api.vo.OrderRangeStatsVO;
import com.myshop.entity.dto.CouponRushPrepareDTO;
import com.myshop.entity.dto.PayInfoDTO;
import com.myshop.entity.dto.PayOrderNotifyDTO;
import com.myshop.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

import java.util.List;

@FeignClient(name = "simlect-order", contextId = "orderFeignClient", path = "/internal/order",
        fallbackFactory = OrderFeignFallbackFactory.class)
public interface OrderFeignClient {

    @PostMapping("/get")
    ResponseVO<OrderBriefVO> getOrder(@RequestBody OrderIdDTO dto);

    @PostMapping("/cancelUnpaidForPayTimeout")
    ResponseVO<Boolean> cancelUnpaidForPayTimeout(@RequestBody OrderIdDTO dto);

    @PostMapping("/confirmReceipt")
    ResponseVO<Boolean> confirmReceipt(@RequestBody OrderIdDTO dto);

    @PostMapping("/onConfirmed")
    ResponseVO<Void> onConfirmed(@RequestBody OrderIdDTO dto);

    @PostMapping("/paySuccess")
    ResponseVO<Void> paySuccess(@RequestBody PayOrderNotifyDTO dto);

    @PostMapping("/prepareCouponRush")
    ResponseVO<CouponRushPrepareDTO> prepareCouponRush(@RequestBody CouponRushPrepareRequestDTO dto);

    @PostMapping("/postCouponRushOrder")
    ResponseVO<PayInfoDTO> postCouponRushOrder(@RequestBody CouponRushPayRequestDTO dto);

    @PostMapping("/syncPaidCouponRushUserCoupons")
    ResponseVO<Void> syncPaidCouponRushUserCoupons(@RequestBody UserIdDTO dto);

    @PostMapping("/cancelOrder")
    ResponseVO<Void> cancelOrder(@RequestBody OrderIdDTO dto);

    @PostMapping("/stats/range")
    ResponseVO<OrderRangeStatsVO> aggregateRange(@RequestBody OrderStatsRangeDTO dto);

    @PostMapping("/stats/daily")
    ResponseVO<List<OrderDailyStatsVO>> aggregateDaily(@RequestBody OrderStatsRangeDTO dto);

    @PostMapping("/tool/addAllWaitPayToDelayQueue")
    ResponseVO<Void> addAllWaitPayToDelayQueue();
}
