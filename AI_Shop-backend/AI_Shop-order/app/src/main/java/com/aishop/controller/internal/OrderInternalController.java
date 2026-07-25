package com.aishop.controller.internal;

import com.aishop.api.dto.CouponRushPayRequestDTO;
import com.aishop.api.dto.CouponRushPrepareRequestDTO;
import com.aishop.api.dto.OrderIdDTO;
import com.aishop.api.dto.OrderStatsRangeDTO;
import com.aishop.api.dto.UserIdDTO;
import com.aishop.api.vo.OrderBriefVO;
import com.aishop.api.vo.OrderDailyStatsVO;
import com.aishop.api.vo.OrderRangeStatsVO;
import com.aishop.biz.OrderInfoService;
import com.aishop.biz.OrderInternalService;
import com.aishop.controller.ABaseController;
import com.aishop.api.dto.CouponRushPrepareDTO;
import com.aishop.api.dto.PayInfoDTO;
import com.aishop.api.dto.PayOrderNotifyDTO;
import com.aishop.api.enums.OrderStatusEnum;
import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.query.OrderInfoQuery;
import com.aishop.entity.vo.ResponseVO;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/internal/order")
public class OrderInternalController extends ABaseController {

    @Resource
    private OrderInternalService orderInternalService;

    @Resource
    private OrderInfoService orderInfoService;

    @PostMapping("/get")
    public ResponseVO<OrderBriefVO> getOrder(@RequestBody OrderIdDTO dto) {
        return getSuccessResponseVO(orderInternalService.getOrder(dto));
    }

    @PostMapping("/cancelUnpaidForPayTimeout")
    public ResponseVO<Boolean> cancelUnpaidForPayTimeout(@RequestBody OrderIdDTO dto) {
        return getSuccessResponseVO(orderInternalService.cancelUnpaidForPayTimeout(dto));
    }

    @PostMapping("/confirmReceipt")
    public ResponseVO<Boolean> confirmReceipt(@RequestBody OrderIdDTO dto) {
        return getSuccessResponseVO(orderInternalService.confirmReceipt(dto));
    }

    @PostMapping("/onConfirmed")
    public ResponseVO<Void> onConfirmed(@RequestBody OrderIdDTO dto) {
        orderInternalService.onConfirmed(dto);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/paySuccess")
    public ResponseVO<Void> paySuccess(@RequestBody PayOrderNotifyDTO dto) {
        orderInternalService.paySuccess(dto);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/prepareCouponRush")
    public ResponseVO<CouponRushPrepareDTO> prepareCouponRush(
            @RequestBody CouponRushPrepareRequestDTO dto,
            @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey) {
        return getSuccessResponseVO(orderInternalService.prepareCouponRush(dto, idempotencyKey));
    }

    @PostMapping("/postCouponRushOrder")
    public ResponseVO<PayInfoDTO> postCouponRushOrder(
            @RequestBody CouponRushPayRequestDTO dto,
            @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey) {
        return getSuccessResponseVO(orderInternalService.postCouponRushOrder(dto, idempotencyKey));
    }

    @PostMapping("/syncPaidCouponRushUserCoupons")
    public ResponseVO<Void> syncPaidCouponRushUserCoupons(@RequestBody UserIdDTO dto) {
        orderInternalService.syncPaidCouponRushUserCoupons(dto);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/cancelOrder")
    public ResponseVO<Void> cancelOrder(@RequestBody OrderIdDTO dto) {
        orderInternalService.cancelOrder(dto);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/stats/range")
    public ResponseVO<OrderRangeStatsVO> aggregateRange(@RequestBody OrderStatsRangeDTO dto) {
        return getSuccessResponseVO(orderInternalService.aggregateRange(dto));
    }

    @PostMapping("/stats/daily")
    public ResponseVO<List<OrderDailyStatsVO>> aggregateDaily(@RequestBody OrderStatsRangeDTO dto) {
        return getSuccessResponseVO(orderInternalService.aggregateDaily(dto));
    }

    @PostMapping("/tool/addAllWaitPayToDelayQueue")
    public ResponseVO<Void> addAllWaitPayToDelayQueue() {
        OrderInfoQuery orderInfoQuery = new OrderInfoQuery();
        orderInfoQuery.setOrderStatus(OrderStatusEnum.WAIT_PAYMENT.getStatus());
        List<OrderInfo> orderInfoList = orderInfoService.findListByParam(orderInfoQuery);
        orderInfoService.addAllOrderToDelayQueue(orderInfoList);
        return getSuccessResponseVO(null);
    }
}
