package com.aishop.component;

import com.aishop.constants.Constants;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.api.dto.NotificationMessageDTO;
import com.aishop.entity.po.UserNotification;
import com.aishop.redis.RedisUtils;
import com.aishop.biz.UserNotificationService;
import com.rabbitmq.client.Channel;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.codec.digest.DigestUtils;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.util.Date;

@Slf4j
@Component
public class RabbitMQNotifyListenerComponent {

    @Resource
    private UserNotificationService userNotificationService;
    @Resource
    private RedisUtils redisUtils;
    @Resource
    private NotifyPushPublisher notifyPushPublisher;
    @Resource
    private MqListenerHelper mqListenerHelper;

    @RabbitListener(queues = RabbitMQConfig.NOTIFY_QUEUE, ackMode = "MANUAL")
    public void handleNotify(NotificationMessageDTO message, Channel channel, Message mqMessage) {
        long deliveryTag = mqMessage.getMessageProperties().getDeliveryTag();
        try {
            if (!mqListenerHelper.tryBeginConsume(mqMessage, MqListenerHelper.CONSUME_IDEMPOTENCY_TTL_HIGH_SECONDS)) {
                mqListenerHelper.ackCompletedOrDeferBusy(
                        channel, deliveryTag, mqMessage, RabbitMQConfig.NOTIFY_QUEUE);
                return;
            }
            if (message == null || message.getUserId() == null || message.getTitle() == null) {
                throw new IllegalArgumentException("通知消息字段不完整");
            }
            UserNotification notification = new UserNotification();
            notification.setNotificationId(stableNotificationId(mqMessage));
            notification.setUserId(message.getUserId());
            notification.setTitle(message.getTitle());
            notification.setContent(message.getContent());
            notification.setBizType(message.getBizType());
            notification.setBizId(message.getBizId());
            notification.setReadStatus(0);
            notification.setCreateTime(new Date());

            boolean inserted = userNotificationService.insertIfAbsent(notification);
            if (inserted) {
                markPopupAndPush(notification);
            }
            mqListenerHelper.clearConsumeRetry(RabbitMQConfig.NOTIFY_QUEUE, mqMessage);
            channel.basicAck(deliveryTag, false);
        } catch (Exception e) {
            log.error("处理通知消息失败", e);
            try {
                mqListenerHelper.nackWithRetryOrDlq(
                        channel,
                        deliveryTag,
                        mqMessage,
                        RabbitMQConfig.NOTIFY_QUEUE,
                        message,
                        e);
            } catch (IOException ex) {
                log.error("通知 NACK 失败", ex);
            }
        }
    }

    private String stableNotificationId(Message mqMessage) {
        String idempotencyKey = mqListenerHelper.resolveIdempotencyKey(mqMessage);
        if (idempotencyKey == null || idempotencyKey.isBlank()) {
            throw new IllegalArgumentException("通知消息缺少幂等键");
        }
        return "N" + DigestUtils.sha256Hex(idempotencyKey).substring(0, 30);
    }

    private void markPopupAndPush(UserNotification notification) {
        try {
            String bizType = notification.getBizType();
            if ("rush_coupon".equals(bizType) || "logistics".equals(bizType)
                    || "coupon_expire".equals(bizType) || "sign_reward".equals(bizType)) {
                String popupKey = Constants.REDIS_KEY_USER_POPUP_NOTIFY + notification.getUserId()
                        + ":" + notification.getNotificationId();
                redisUtils.setex(popupKey, "1", 7 * 24 * 3600);
            }
        } catch (Exception e) {
            log.warn("通知弹窗标记失败 notificationId={}", notification.getNotificationId(), e);
        }
        try {
            notifyPushPublisher.push(notification);
        } catch (Exception e) {
            log.warn("通知 WS 推送失败 notificationId={}", notification.getNotificationId(), e);
        }
    }
}
