package com.simlect.api;

import com.simlect.api.dto.CouponRushPayRequestDTO;
import com.simlect.api.dto.CouponRushPrepareRequestDTO;
import com.simlect.api.dto.OrderIdDTO;
import com.simlect.api.dto.OrderStatsRangeDTO;
import com.simlect.api.dto.UserIdDTO;
import com.simlect.api.fallback.OrderFeignFallbackFactory;
import com.simlect.api.vo.OrderBriefVO;
import com.simlect.api.vo.OrderDailyStatsVO;
import com.simlect.api.vo.OrderRangeStatsVO;
import com.simlect.api.dto.CouponRushPrepareDTO;
import com.simlect.api.dto.PayInfoDTO;
import com.simlect.api.dto.PayOrderNotifyDTO;
import com.simlect.entity.vo.ResponseVO;
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
