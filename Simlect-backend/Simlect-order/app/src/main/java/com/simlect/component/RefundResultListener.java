package com.simlect.component;

import com.rabbitmq.client.Channel;
import com.simlect.api.dto.RefundStockResultDTO;
import com.simlect.biz.RefundSagaTransactionService;
import com.simlect.constants.RabbitMQConfig;
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

    @RabbitListener(queues = RabbitMQConfig.REFUND_RESULT_QUEUE, ackMode = "MANUAL")
    public void complete(RefundStockResultDTO result, Channel channel, Message message)
            throws IOException {
        long deliveryTag = message.getMessageProperties().getDeliveryTag();
        try {
            transactionService.markCompleted(result.getRefundRequestId());
            channel.basicAck(deliveryTag, false);
        } catch (Exception e) {
            log.error("退款完成回执处理失败, refundRequestId={}", result.getRefundRequestId(), e);
            channel.basicNack(deliveryTag, false, false);
        }
    }
}
