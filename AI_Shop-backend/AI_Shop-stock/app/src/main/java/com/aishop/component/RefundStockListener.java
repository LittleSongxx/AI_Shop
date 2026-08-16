package com.aishop.component;

import com.rabbitmq.client.Channel;
import com.aishop.api.dto.RefundStockRestoreDTO;
import com.aishop.api.dto.RefundStockResultDTO;
import com.aishop.biz.SkuStockService;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.ReliableMessageSender;
import com.aishop.support.MqIdempotencyKeys;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

import java.io.IOException;

@Slf4j
@Component
public class RefundStockListener {

    @Resource
    private SkuStockService skuStockService;
    @Resource
    private ReliableMessageSender reliableMessageSender;
    @Resource
    private MqListenerHelper mqListenerHelper;

    @RabbitListener(queues = RabbitMQConfig.REFUND_STOCK_QUEUE, ackMode = "MANUAL")
    public void restore(RefundStockRestoreDTO payload, Channel channel, Message message)
            throws IOException {
        long deliveryTag = message.getMessageProperties().getDeliveryTag();
        if (!mqListenerHelper.tryBeginConsume(
                message, MqListenerHelper.CONSUME_IDEMPOTENCY_TTL_HIGH_SECONDS)) {
            mqListenerHelper.ackCompletedOrDeferBusy(
                    channel, deliveryTag, message, RabbitMQConfig.REFUND_STOCK_QUEUE);
            return;
        }
        try {
            if (payload == null || payload.getRefundRequestId() == null) {
                throw new IllegalArgumentException("退款库存恢复消息字段不完整");
            }
            skuStockService.restoreRefundStock(payload);
            RefundStockResultDTO result = new RefundStockResultDTO(
                    payload.getRefundRequestId(), payload.getBusinessKey());
            reliableMessageSender.replaySend(
                    RabbitMQConfig.REFUND_EXCHANGE,
                    RabbitMQConfig.REFUND_RESULT_KEY,
                    result,
                    MqIdempotencyKeys.refundResult(payload.getRefundRequestId()));
            mqListenerHelper.clearConsumeRetry(RabbitMQConfig.REFUND_STOCK_QUEUE, message);
            channel.basicAck(deliveryTag, false);
        } catch (Exception e) {
            log.error("退款库存恢复失败, refundRequestId={}",
                    payload == null ? null : payload.getRefundRequestId(), e);
            mqListenerHelper.nackWithRetryOrDlq(
                    channel,
                    deliveryTag,
                    message,
                    RabbitMQConfig.REFUND_STOCK_QUEUE,
                    payload,
                    e);
        }
    }
}
