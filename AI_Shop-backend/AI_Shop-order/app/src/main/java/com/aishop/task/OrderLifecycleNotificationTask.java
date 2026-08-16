package com.aishop.task;

import com.aishop.component.OrderNotificationPublisher;
import com.aishop.entity.po.OrderInfo;
import com.aishop.mappers.OrderInfoMapper;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/** Sends bounded, idempotent lifecycle reminders through the existing user notification outbox. */
@Slf4j
@Component
public class OrderLifecycleNotificationTask {

    @Resource
    private OrderInfoMapper<OrderInfo, ?> orderInfoMapper;
    @Resource
    private OrderNotificationPublisher orderNotificationPublisher;

    @Value("${order.notification.delay-enabled:true}")
    private boolean delayEnabled;
    @Value("${order.notification.delay-hours:24}")
    private int delayHours;
    @Value("${order.notification.delay-batch-size:100}")
    private int batchSize;

    @Scheduled(fixedDelayString = "${order.notification.delay-scan-ms:1800000}")
    public void notifyDelayedOrders() {
        if (!delayEnabled) {
            return;
        }
        int boundedHours = Math.max(1, Math.min(delayHours, 720));
        int boundedBatch = Math.max(1, Math.min(batchSize, 200));
        List<OrderInfo> orders = orderInfoMapper.selectDelayedPaidOrders(boundedHours, boundedBatch);
        if (orders == null || orders.isEmpty()) {
            return;
        }
        int sent = 0;
        for (OrderInfo order : orders) {
            if (order == null || StringTools.isEmpty(order.getUserId())
                    || StringTools.isEmpty(order.getOrderId())) {
                continue;
            }
            orderNotificationPublisher.send(
                    order.getUserId(),
                    "订单发货延迟提醒",
                    "订单 " + order.getOrderId() + " 尚未发货，我们已记录延迟状态，请留意后续物流更新。",
                    "order_delay",
                    order.getOrderId());
            sent++;
        }
        log.info("订单延迟提醒扫描完成，候选={}，已提交通知={}", orders.size(), sent);
    }
}
