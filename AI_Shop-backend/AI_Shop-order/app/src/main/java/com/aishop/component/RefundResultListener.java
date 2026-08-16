package com.aishop.component;

import com.rabbitmq.client.Channel;
import com.aishop.api.dto.RefundStockResultDTO;
import com.aishop.biz.RefundSagaTransactionService;
import com.aishop.constants.RabbitMQConfig;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

import java.io.IOException;

@Slf4j
@Component
public class RefundResultListener {

    @Resource
    private RefundSagaTransactionService transactionService;
    @Resource
    private MqListenerHelper mqListenerHelper;

    @RabbitListener(queues = RabbitMQConfig.REFUND_RESULT_QUEUE, ackMode = "MANUAL")
    public void complete(RefundStockResultDTO result, Channel channel, Message message)
            throws IOException {
        long deliveryTag = message.getMessageProperties().getDeliveryTag();
        if (!mqListenerHelper.tryBeginConsume(
                message, MqListenerHelper.CONSUME_IDEMPOTENCY_TTL_HIGH_SECONDS)) {
            mqListenerHelper.ackCompletedOrDeferBusy(
                    channel, deliveryTag, message, RabbitMQConfig.REFUND_RESULT_QUEUE);
            return;
        }
        try {
            if (result == null || result.getRefundRequestId() == null) {
                throw new IllegalArgumentException("退款结果消息字段不完整");
            }
            transactionService.markCompleted(result.getRefundRequestId());
            mqListenerHelper.clearConsumeRetry(RabbitMQConfig.REFUND_RESULT_QUEUE, message);
            channel.basicAck(deliveryTag, false);
        } catch (Exception e) {
            log.error("退款完成回执处理失败, refundRequestId={}",
                    result == null ? null : result.getRefundRequestId(), e);
            mqListenerHelper.nackWithRetryOrDlq(
                    channel,
                    deliveryTag,
                    message,
                    RabbitMQConfig.REFUND_RESULT_QUEUE,
                    result,
                    e);
        }
    }
}
