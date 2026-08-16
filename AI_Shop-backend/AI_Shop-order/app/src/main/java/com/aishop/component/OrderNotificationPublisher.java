package com.aishop.component;

import com.aishop.api.dto.NotificationMessageDTO;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.TransactionalMqSender;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.support.MqIdempotencyKeys;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Component;

@Component
public class OrderNotificationPublisher {

    @Resource
    private TransactionalMqSender transactionalMqSender;

    public void send(String userId, String title, String content, String bizType, String bizId) {
        if (StringTools.isEmpty(userId) || StringTools.isEmpty(title)) {
            return;
        }
        transactionalMqSender.sendAfterCommit(
                RabbitMQConfig.NOTIFY_EXCHANGE,
                RabbitMQConfig.NOTIFY_KEY,
                new NotificationMessageDTO(userId, title, content, bizType, bizId),
                MqIdempotencyKeys.notification(userId, bizType, bizId),
                MessageReliabilityLevelEnum.HIGH);
    }
}
