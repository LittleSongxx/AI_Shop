package com.myshop.component;

import com.alibaba.fastjson.JSON;
import com.myshop.constants.Constants;
import com.myshop.entity.dto.MessageSendDTO;
import com.myshop.entity.enums.DateTimePatternEnum;
import com.myshop.entity.po.UserNotification;
import com.myshop.utils.DateUtil;
import com.myshop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RTopic;
import org.redisson.api.RedissonClient;
import org.redisson.client.codec.StringCodec;
import org.springframework.stereotype.Component;

@Component
@Slf4j
public class NotifyPushPublisher {

    @Resource
    private RedissonClient redissonClient;

    public void push(UserNotification notification) {
        if (notification == null || StringTools.isEmpty(notification.getUserId())) {
            return;
        }
        try {
            MessageSendDTO dto = new MessageSendDTO();
            dto.setMessageType(Constants.WS_MESSAGE_TYPE_NOTIFY);
            dto.setUserId(notification.getUserId());
            dto.setNotificationId(notification.getNotificationId());
            dto.setTitle(notification.getTitle());
            dto.setContent(notification.getContent());
            dto.setBizType(notification.getBizType());
            dto.setBizId(notification.getBizId());
            if (notification.getCreateTime() != null) {
                dto.setCreateTime(DateUtil.format(notification.getCreateTime(),
                        DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern()));
            }
            RTopic topic = redissonClient.getTopic(Constants.WS_MESSAGE_TOPIC);
            topic.publish(dto);
            RTopic agentTopic = redissonClient.getTopic(
                    Constants.WS_MESSAGE_TOPIC_AGENT, StringCodec.INSTANCE);
            agentTopic.publish(JSON.toJSONString(dto));
            log.debug("通知 WS 广播已发布 userId={}, notificationId={}",
                    notification.getUserId(), notification.getNotificationId());
        } catch (Exception e) {
            log.warn("通知 WS 广播失败 userId={}, notificationId={}",
                    notification.getUserId(), notification.getNotificationId(), e);
        }
    }
}
