package com.simlect.task;

import com.simlect.api.support.PayFeignSupport;
import com.simlect.component.RedisComponent;
import com.simlect.entity.config.AppConfig;
import com.simlect.api.dto.PayOrderNotifyDTO;
import com.simlect.api.enums.OrderStatusEnum;
import com.simlect.api.enums.PayChannelEnum;
import com.simlect.entity.po.OrderInfo;
import com.simlect.entity.query.OrderInfoQuery;
import com.simlect.biz.OrderInfoService;
import com.simlect.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
@Slf4j
public class PayOrderTask {

    @Resource
    private AppConfig appConfig;

    @Resource
    private OrderInfoService orderInfoService;

    @Resource
    private PayFeignSupport payFeignSupport;

    @Resource
    private RedisComponent redisComponent;

    @Scheduled(fixedDelay = 5000)
    public void pollPayOrders() {
        if (!appConfig.getAutoCheckpay()) {
            return;
        }
        try {
            OrderInfoQuery query = new OrderInfoQuery();
            query.setOrderStatus(OrderStatusEnum.WAIT_PAYMENT.getStatus());
            List<OrderInfo> orderInfoList = orderInfoService.findListByParam(query);
            for (OrderInfo orderInfo : orderInfoList) {
                String payOrderId = orderInfo.getPayOrderId();
                if (StringTools.isEmpty(payOrderId)) {
                    continue;
                }
                if (!redisComponent.isPayTradeInitiated(payOrderId)) {
                    continue;
                }
                PayChannelEnum payChannelEnum = PayChannelEnum.resolve(orderInfo.getPayChannel());
                if (payChannelEnum == null) {
                    continue;
                }
                PayOrderNotifyDTO payOrderNotifyDTO = payFeignSupport.queryOrder(payOrderId, payChannelEnum.getPayScene());
                if (payOrderNotifyDTO == null) {
                    continue;
                }
                orderInfoService.paySuccess(payOrderNotifyDTO);
            }
        } catch (Exception e) {
            log.error("查询支付信息异常", e);
        }
    }
}
