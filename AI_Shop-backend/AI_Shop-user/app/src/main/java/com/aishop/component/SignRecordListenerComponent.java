package com.aishop.component;

import com.aishop.constants.RabbitMQConfig;
import com.aishop.api.dto.SignRecordMessageDTO;
import com.aishop.entity.po.UserSignRecord;
import com.aishop.entity.po.UserSignRecordDetail;
import com.aishop.mappers.UserSignRecordDetailMapper;
import com.aishop.mappers.UserSignRecordMapper;
import com.aishop.utils.StringTools;
import com.rabbitmq.client.Channel;
import jakarta.annotation.Resource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.util.Date;

@Component
public class SignRecordListenerComponent {

    private static final Logger log = LoggerFactory.getLogger(SignRecordListenerComponent.class);

    @Resource
    private UserSignRecordMapper<UserSignRecord, com.aishop.entity.query.UserSignRecordQuery> userSignRecordMapper;
    @Resource
    private UserSignRecordDetailMapper<UserSignRecordDetail, com.aishop.entity.query.UserSignRecordDetailQuery> userSignRecordDetailMapper;
    @Resource
    private MqListenerHelper mqListenerHelper;

    @RabbitListener(queues = RabbitMQConfig.SIGN_RECORD_QUEUE, ackMode = "MANUAL")
    public void handleSignRecord(SignRecordMessageDTO message, Channel channel, Message mqMessage) throws IOException {
        long deliveryTag = mqMessage.getMessageProperties().getDeliveryTag();
        if (!mqListenerHelper.tryBeginConsume(mqMessage, MqListenerHelper.CONSUME_IDEMPOTENCY_TTL_HIGH_SECONDS)) {
            mqListenerHelper.ackCompletedOrDeferBusy(
                    channel, deliveryTag, mqMessage, RabbitMQConfig.SIGN_RECORD_QUEUE);
            return;
        }
        try {
            if (message == null || message.getUserId() == null) {
                mqListenerHelper.clearConsumeRetry(RabbitMQConfig.SIGN_RECORD_QUEUE, mqMessage);
                channel.basicAck(deliveryTag, false);
                return;
            }
            UserSignRecord record = new UserSignRecord();
            record.setUserId(message.getUserId());
            record.setContinuousDays(message.getContinuousDays());
            record.setTotalSignDays(message.getTotalSignDays());
            record.setUsedCount(message.getUsedCount());
            userSignRecordMapper.insertOrUpdate(record);

            persistSignDetail(message);

            mqListenerHelper.clearConsumeRetry(RabbitMQConfig.SIGN_RECORD_QUEUE, mqMessage);
            channel.basicAck(deliveryTag, false);
            log.info("签到记录已异步落库, userId: {}, signDate: {}, continuousDays: {}, totalSignDays: {}, usedCount: {}",
                    message.getUserId(), message.getSignDate(), message.getContinuousDays(),
                    message.getTotalSignDays(), message.getUsedCount());
        } catch (Exception e) {
            log.error("签到记录异步落库失败", e);
            mqListenerHelper.nackWithRetryOrDlq(channel, deliveryTag, mqMessage,
                    RabbitMQConfig.SIGN_RECORD_QUEUE, message, e);
        }
    }

    private void persistSignDetail(SignRecordMessageDTO message) {
        if (StringTools.isEmpty(message.getSignDate()) || message.getSignDate().length() != 8) {
            return;
        }
        UserSignRecordDetail detail = new UserSignRecordDetail();
        detail.setUserId(message.getUserId());
        detail.setSignDate(message.getSignDate());
        detail.setSignType(message.getSignType() == null ? 0 : message.getSignType());
        detail.setCreateTime(new Date());
        userSignRecordDetailMapper.insertIgnore(detail);
    }
}
