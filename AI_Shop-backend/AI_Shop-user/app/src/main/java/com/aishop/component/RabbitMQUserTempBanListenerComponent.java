package com.aishop.component;

import com.aishop.constants.RabbitMQConfig;
import com.aishop.api.dto.UserTempBanDTO;
import com.aishop.component.UserTempBanService;
import com.rabbitmq.client.Channel;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

import java.io.IOException;

@Slf4j
@Component
public class RabbitMQUserTempBanListenerComponent {

    @Resource
    private UserTempBanService userTempBanService;
    @Resource
    private MqListenerHelper mqListenerHelper;

    @RabbitListener(queues = RabbitMQConfig.USER_TEMP_BAN_DEAD_QUEUE, ackMode = "MANUAL")
    public void onTempBanExpire(UserTempBanDTO dto, Channel channel, Message message) throws IOException {
        long deliveryTag = message.getMessageProperties().getDeliveryTag();
        if (!mqListenerHelper.tryBeginConsume(
                message, MqListenerHelper.CONSUME_IDEMPOTENCY_TTL_STANDARD_SECONDS)) {
            mqListenerHelper.ackCompletedOrDeferBusy(
                    channel, deliveryTag, message, RabbitMQConfig.USER_TEMP_BAN_DEAD_QUEUE);
            return;
        }
        try {
            if (dto == null || dto.getUserId() == null) {
                throw new IllegalArgumentException("临时解封消息字段不完整");
            }
            userTempBanService.tryAutoUnban(dto);
            mqListenerHelper.clearConsumeRetry(RabbitMQConfig.USER_TEMP_BAN_DEAD_QUEUE, message);
            channel.basicAck(deliveryTag, false);
        } catch (Exception e) {
            log.error("临时封禁解封失败 userId={}", dto == null ? null : dto.getUserId(), e);
            mqListenerHelper.nackWithRetryOrDlq(
                    channel,
                    deliveryTag,
                    message,
                    RabbitMQConfig.USER_TEMP_BAN_DEAD_QUEUE,
                    dto,
                    e);
        }
    }
}
