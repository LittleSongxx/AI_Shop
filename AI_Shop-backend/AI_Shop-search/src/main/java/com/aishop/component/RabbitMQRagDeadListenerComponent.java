package com.aishop.component;

import com.aishop.constants.RabbitMQConfig;
import com.aishop.entity.dto.RagDataDTO;
import com.rabbitmq.client.Channel;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import io.micrometer.core.instrument.MeterRegistry;

import java.io.IOException;
import java.util.Locale;

@Component
@Slf4j
public class RabbitMQRagDeadListenerComponent {

    @Resource
    private RedisComponent redisComponent;
    @Resource
    private MqConsumeFailureRecorder mqConsumeFailureRecorder;
    @Autowired(required = false)
    private MeterRegistry meterRegistry;

    @RabbitListener(queues = RabbitMQConfig.RAG_DEAD_QUEUE, ackMode = "MANUAL")
    public void handleRagDLQ(RagDataDTO dto, Channel channel, Message message) throws IOException {
        long deliveryTag = message.getMessageProperties().getDeliveryTag();
        observe("received", dto);
        try {
            log.error("RAG同步最终失败: type={}, dataId={}",
                    dto != null ? dto.getType() : "null",
                    dto != null ? dto.getDataId() : "null");
            redisComponent.addRagFailRecord(dto);
            mqConsumeFailureRecorder.record(
                    RabbitMQConfig.RAG_DEAD_QUEUE,
                    message,
                    dto,
                    new com.aishop.exception.BusinessException("RAG 同步重试耗尽，进入死信队列"));
            // 2. 确定性失败（dataId 为空/格式错误）→ 告警
            if (dto == null || dto.getDataId() == null || dto.getType() == null) {
                log.error("【需人工介入】RAG消息数据异常: {}", dto);
            }

            channel.basicAck(deliveryTag, false);
            observe("recorded", dto);
        } catch (Exception e) {
            log.error("DLQ处理异常", e);
            observe("handler_error", dto);
            channel.basicNack(deliveryTag, false, false);
        }
    }

    private void observe(String result, RagDataDTO dto) {
        if (meterRegistry == null) {
            return;
        }
        String type = dto == null || dto.getType() == null
                ? "unknown" : switch (dto.getType().toLowerCase(Locale.ROOT)) {
                    case "faq" -> "faq";
                    case "product" -> "product";
                    default -> "other";
                };
        meterRegistry.counter("aishop.rag.dlq.total", "result", result, "type", type)
                .increment();
    }
}
