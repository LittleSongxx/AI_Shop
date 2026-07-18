package com.myshop.component;

import com.myshop.api.support.OrderFeignSupport;
import com.myshop.api.vo.OrderBriefVO;
import com.myshop.constants.RabbitMQConfig;
import com.myshop.entity.dto.RushingCouponMessageDTO;
import com.myshop.entity.enums.OrderStatusEnum;
import com.myshop.entity.enums.UserCouponStatusEnum;
import com.myshop.entity.po.UserCoupon;
import com.myshop.biz.DiscountCouponService;
import com.myshop.biz.UserCouponService;
import com.myshop.utils.StringTools;
import com.rabbitmq.client.Channel;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;

@Component
@Slf4j
public class RabbitMQOrderDeadListListenerComponent {

    @Resource
    private DiscountCouponService discountCouponService;
    @Resource
    private UserCouponService userCouponService;
    @Resource
    private OrderFeignSupport orderFeignSupport;
    @Resource
    private MqListenerHelper mqListenerHelper;

    @RabbitListener(queues = RabbitMQConfig.RUSHING_DEAD_QUEUE, ackMode = "MANUAL")
    @Transactional(rollbackFor = Exception.class)
    public void handleDeadOrder(RushingCouponMessageDTO message, Channel channel, Message mqMessage) throws IOException {
        Long deliveryTag = mqMessage.getMessageProperties().getDeliveryTag();
        if (!mqListenerHelper.tryBeginConsume(mqMessage, MqListenerHelper.CONSUME_IDEMPOTENCY_TTL_STANDARD_SECONDS)) {
            channel.basicAck(deliveryTag, false);
            return;
        }
        try {
            String userCouponId = message.getUserCouponId();
            String userId = message.getUserId();
            String couponId = message.getCouponId();

            log.info("抢购死信兜底: userCouponId={}", userCouponId);

            UserCoupon userCoupon = userCouponService.getUserCouponByUserCouponId(userCouponId);
            OrderBriefVO orderInfo = StringTools.isEmpty(message.getOrderId())
                    ? null
                    : orderFeignSupport.getOrder(message.getOrderId());

            if (StringTools.isEmpty(message.getOrderId())) {
                if (userCoupon == null) {
                    log.warn("抢购预占超时未建单，回补库存: couponId={}, userId={}", couponId, userId);
                    discountCouponService.releaseRushCouponReserve(couponId, userId);
                }
                channel.basicAck(deliveryTag, false);
                return;
            }

            if (userCoupon == null && orderInfo == null) {
                log.warn("建单未落库，仅回滚 Redis 预占: {}", userCouponId);
                discountCouponService.releaseRushRedisReserve(couponId, userId);
                channel.basicAck(deliveryTag, false);
                return;
            }

            if (orderInfo != null
                    && OrderStatusEnum.WAIT_PAYMENT.getStatus().equals(orderInfo.getOrderStatus())) {
                orderFeignSupport.cancelOrder(message.getOrderId(), null);
            } else if (userCoupon != null
                    && UserCouponStatusEnum.CANT.getStatus().equals(userCoupon.getStatus())
                    && orderInfo == null) {
                log.warn("用户券存在但订单缺失，仅回滚 Redis: {}", userCouponId);
                discountCouponService.releaseRushRedisReserve(couponId, userId);
            }

            mqListenerHelper.clearConsumeRetry(RabbitMQConfig.RUSHING_DEAD_QUEUE, mqMessage);
            channel.basicAck(deliveryTag, false);
        } catch (Exception e) {
            log.error("抢购死信处理失败", e);
            mqListenerHelper.nackWithRetryOrDlq(channel, deliveryTag, mqMessage,
                    RabbitMQConfig.RUSHING_DEAD_QUEUE, message, e);
        }
    }
}
