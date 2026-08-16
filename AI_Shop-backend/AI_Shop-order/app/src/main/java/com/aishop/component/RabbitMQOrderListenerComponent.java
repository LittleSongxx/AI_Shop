package com.aishop.component;

import com.aishop.api.support.CouponFeignSupport;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.api.dto.RushingCouponMessageDTO;
import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.query.OrderInfoQuery;
import com.aishop.mappers.OrderInfoMapper;
import com.rabbitmq.client.Channel;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.io.IOException;

@Component
@Slf4j
public class RabbitMQOrderListenerComponent {

    @Resource
    private OrderInfoMapper<OrderInfo, OrderInfoQuery> orderInfoMapper;
    @Resource
    private CouponFeignSupport couponFeignSupport;
    @Resource
    private MqListenerHelper mqListenerHelper;

    @RabbitListener(queues = RabbitMQConfig.RUSHING_ORDER_QUEUE, ackMode = "MANUAL")
    public void handleOrder(RushingCouponMessageDTO message, Channel channel, Message mqMessage) {
        Long deliveryTag = mqMessage.getMessageProperties().getDeliveryTag();
        if (!mqListenerHelper.tryBeginConsume(
                mqMessage, MqListenerHelper.CONSUME_IDEMPOTENCY_TTL_STANDARD_SECONDS)) {
            try {
                mqListenerHelper.ackCompletedOrDeferBusy(
                        channel, deliveryTag, mqMessage, RabbitMQConfig.RUSHING_ORDER_QUEUE);
            } catch (IOException e) {
                log.error("抢购订单重复消息结算失败", e);
            }
            return;
        }
        String userCouponId = message == null ? null : message.getUserCouponId();
        try {
            if (message == null || message.getOrderId() == null || userCouponId == null) {
                throw new IllegalArgumentException("抢购订单消息字段不完整");
            }
            if (orderInfoMapper.selectByOrderId(message.getOrderId()) != null
                    || couponFeignSupport.getUserCoupon(userCouponId) != null) {
                mqListenerHelper.clearConsumeRetry(RabbitMQConfig.RUSHING_ORDER_QUEUE, mqMessage);
                channel.basicAck(deliveryTag, false);
                return;
            }
            log.warn("遗留抢购 MQ 消息无对应订单，跳过建单 userCouponId={}", userCouponId);
            mqListenerHelper.clearConsumeRetry(RabbitMQConfig.RUSHING_ORDER_QUEUE, mqMessage);
            channel.basicAck(deliveryTag, false);
        } catch (Exception e) {
            log.error("创建抢购订单失败: {}", userCouponId, e);
            try {
                if (!TransactionSynchronizationManager.isSynchronizationActive()) {
                    mqListenerHelper.nackWithRetryOrDlq(
                            channel,
                            deliveryTag,
                            mqMessage,
                            RabbitMQConfig.RUSHING_ORDER_QUEUE,
                            message,
                            e);
                }
            } catch (IOException ex) {
                log.error("兜底NACK失败", ex);
            }
        }
    }
}
