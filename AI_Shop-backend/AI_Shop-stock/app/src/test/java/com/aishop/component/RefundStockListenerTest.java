package com.aishop.component;

import com.aishop.api.dto.RefundStockRestoreDTO;
import com.aishop.biz.SkuStockService;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.ReliableMessageSender;
import com.rabbitmq.client.Channel;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.test.util.ReflectionTestUtils;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

class RefundStockListenerTest {

    private final SkuStockService skuStockService = mock(SkuStockService.class);
    private final ReliableMessageSender reliableMessageSender = mock(ReliableMessageSender.class);
    private final Channel channel = mock(Channel.class);
    private RefundStockListener listener;

    @BeforeEach
    void setUp() {
        listener = new RefundStockListener();
        ReflectionTestUtils.setField(listener, "skuStockService", skuStockService);
        ReflectionTestUtils.setField(listener, "reliableMessageSender", reliableMessageSender);
    }

    @Test
    void successfulRestorePublishesIdempotentResultBeforeAck() throws Exception {
        RefundStockRestoreDTO payload = payload();

        listener.restore(payload, channel, message(17L));

        verify(skuStockService).restoreRefundStock(payload);
        verify(reliableMessageSender).replaySend(
                eq(RabbitMQConfig.REFUND_EXCHANGE),
                eq(RabbitMQConfig.REFUND_RESULT_KEY),
                any(),
                eq("refund:result:r1"));
        verify(channel).basicAck(17L, false);
        verify(channel, never()).basicNack(17L, false, false);
    }

    @Test
    void restoreFailureNacksWithoutPublishingFalseSuccess() throws Exception {
        RefundStockRestoreDTO payload = payload();
        doThrow(new IllegalStateException("db unavailable"))
                .when(skuStockService).restoreRefundStock(payload);

        listener.restore(payload, channel, message(18L));

        verify(reliableMessageSender, never()).replaySend(any(), any(), any(), any());
        verify(channel).basicNack(18L, false, false);
        verify(channel, never()).basicAck(18L, false);
    }

    @Test
    void resultPublishFailureAlsoNacksForBrokerDeadLetterHandling() throws Exception {
        RefundStockRestoreDTO payload = payload();
        doThrow(new IllegalStateException("publisher confirm failed"))
                .when(reliableMessageSender).replaySend(any(), any(), any(), any());

        listener.restore(payload, channel, message(19L));

        verify(channel).basicNack(19L, false, false);
        verify(channel, never()).basicAck(19L, false);
    }

    private static RefundStockRestoreDTO payload() {
        RefundStockRestoreDTO payload = new RefundStockRestoreDTO();
        payload.setRefundRequestId("r1");
        payload.setBusinessKey("refund:r1");
        payload.setProductId("p1");
        payload.setPropertyValueIdHash("sku1");
        payload.setChangeAmount(2);
        return payload;
    }

    private static Message message(long deliveryTag) {
        MessageProperties properties = new MessageProperties();
        properties.setDeliveryTag(deliveryTag);
        return new Message(new byte[0], properties);
    }
}
