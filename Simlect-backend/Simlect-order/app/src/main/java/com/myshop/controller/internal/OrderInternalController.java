package com.myshop.controller.internal;

import com.myshop.api.dto.CouponRushPayRequestDTO;
import com.myshop.api.dto.CouponRushPrepareRequestDTO;
import com.myshop.api.dto.OrderIdDTO;
import com.myshop.api.dto.OrderStatsRangeDTO;
import com.myshop.api.dto.UserIdDTO;
import com.myshop.api.vo.OrderBriefVO;
import com.myshop.api.vo.OrderDailyStatsVO;
import com.myshop.api.vo.OrderRangeStatsVO;
import com.myshop.biz.OrderInfoService;
import com.myshop.biz.OrderInternalService;
import com.myshop.controller.ABaseController;
import com.myshop.entity.dto.CouponRushPrepareDTO;
import com.myshop.entity.dto.PayInfoDTO;
import com.myshop.entity.dto.PayOrderNotifyDTO;
import com.myshop.entity.enums.OrderStatusEnum;
import com.myshop.entity.po.OrderInfo;
import com.myshop.entity.query.OrderInfoQuery;
import com.myshop.entity.vo.ResponseVO;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
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
    public ResponseVO<CouponRushPrepareDTO> prepareCouponRush(@RequestBody CouponRushPrepareRequestDTO dto) {
        return getSuccessResponseVO(orderInternalService.prepareCouponRush(dto));
    }

    @PostMapping("/postCouponRushOrder")
    public ResponseVO<PayInfoDTO> postCouponRushOrder(@RequestBody CouponRushPayRequestDTO dto) {
        return getSuccessResponseVO(orderInternalService.postCouponRushOrder(dto));
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
