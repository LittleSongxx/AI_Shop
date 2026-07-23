package com.simlect.component;

import com.rabbitmq.client.Channel;
import com.simlect.api.dto.RefundStockRestoreDTO;
import com.simlect.api.dto.RefundStockResultDTO;
import com.simlect.biz.SkuStockService;
import com.simlect.constants.RabbitMQConfig;
import com.simlect.constants.ReliableMessageSender;
import com.simlect.support.MqIdempotencyKeys;
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

    @RabbitListener(queues = RabbitMQConfig.REFUND_STOCK_QUEUE, ackMode = "MANUAL")
    public void restore(RefundStockRestoreDTO payload, Channel channel, Message message)
            throws IOException {
        long deliveryTag = message.getMessageProperties().getDeliveryTag();
        try {
            skuStockService.restoreRefundStock(payload);
            RefundStockResultDTO result = new RefundStockResultDTO(
                    payload.getRefundRequestId(), payload.getBusinessKey());
            reliableMessageSender.replaySend(
                    RabbitMQConfig.REFUND_EXCHANGE,
                    RabbitMQConfig.REFUND_RESULT_KEY,
                    result,
                    MqIdempotencyKeys.refundResult(payload.getRefundRequestId()));
            channel.basicAck(deliveryTag, false);
        } catch (Exception e) {
            log.error("退款库存恢复失败, refundRequestId={}", payload.getRefundRequestId(), e);
            channel.basicNack(deliveryTag, false, false);
        }
    }
}
