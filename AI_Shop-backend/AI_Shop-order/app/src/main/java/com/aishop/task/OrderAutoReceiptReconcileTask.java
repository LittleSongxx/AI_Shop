package com.aishop.task;

import com.aishop.biz.OrderInfoService;
import com.aishop.entity.po.OrderInfo;
import com.aishop.mappers.OrderInfoMapper;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * Database reconciliation for long-lived broker timers. The conditional
 * status update in confirmOrderReceipt makes concurrent service instances safe.
 */
@Slf4j
@Component
@ConditionalOnProperty(
        name = "order.lifecycle.auto-receipt-reconcile-enabled",
        havingValue = "true",
        matchIfMissing = true)
public class OrderAutoReceiptReconcileTask {

    @Resource
    private OrderInfoMapper<OrderInfo, ?> orderInfoMapper;
    @Resource
    private OrderInfoService orderInfoService;

    @Value("${order.confirm.minute:10080}")
    private int confirmMinutes;
    @Value("${order.lifecycle.auto-receipt-batch-size:100}")
    private int batchSize;

    @Scheduled(fixedDelayString = "${order.lifecycle.auto-receipt-scan-ms:600000}")
    public void reconcile() {
        int boundedMinutes = Math.max(1, confirmMinutes);
        int boundedBatch = Math.max(1, Math.min(batchSize, 500));
        List<OrderInfo> candidates =
                orderInfoMapper.selectAutoReceivableOrders(boundedMinutes, boundedBatch);
        if (candidates == null || candidates.isEmpty()) {
            return;
        }
        int confirmed = 0;
        for (OrderInfo order : candidates) {
            try {
                if (orderInfoService.confirmOrderReceipt(null, order.getOrderId())) {
                    orderInfoService.onOrderConfirmed(order.getUserId(), order.getOrderId());
                    confirmed++;
                }
            } catch (Exception e) {
                log.error("自动收货数据库对账失败 orderId={}", order.getOrderId(), e);
            }
        }
        log.info("自动收货数据库对账完成 candidates={}, confirmed={}", candidates.size(), confirmed);
    }
}
