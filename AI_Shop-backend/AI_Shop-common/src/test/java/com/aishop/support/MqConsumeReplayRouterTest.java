package com.aishop.support;

import com.aishop.constants.RabbitMQConfig;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class MqConsumeReplayRouterTest {

    @Test
    void userGrowthQueueReplaysToThePrimaryExchangeRoute() {
        assertEquals(
                new MqConsumeReplayRouter.Target(
                        RabbitMQConfig.USER_GROWTH_EXCHANGE,
                        RabbitMQConfig.USER_GROWTH_KEY),
                MqConsumeReplayRouter.resolve(RabbitMQConfig.USER_GROWTH_QUEUE));
        assertEquals(
                new MqConsumeReplayRouter.Target(
                        RabbitMQConfig.USER_GROWTH_EXCHANGE,
                        RabbitMQConfig.USER_GROWTH_KEY),
                MqConsumeReplayRouter.resolve(RabbitMQConfig.USER_GROWTH_DEAD_QUEUE));
    }

    @Test
    void refundAndRushQueuesReplayToTheirBusinessRoutes() {
        assertEquals(
                new MqConsumeReplayRouter.Target(
                        RabbitMQConfig.REFUND_EXCHANGE,
                        RabbitMQConfig.REFUND_STOCK_KEY),
                MqConsumeReplayRouter.resolve(RabbitMQConfig.REFUND_STOCK_DEAD_QUEUE));
        assertEquals(
                new MqConsumeReplayRouter.Target(
                        RabbitMQConfig.REFUND_EXCHANGE,
                        RabbitMQConfig.REFUND_RESULT_KEY),
                MqConsumeReplayRouter.resolve(RabbitMQConfig.REFUND_RESULT_QUEUE));
        assertEquals(
                new MqConsumeReplayRouter.Target(
                        RabbitMQConfig.RUSHING_EXCHANGE,
                        RabbitMQConfig.RUSHING_ORDER_KEY),
                MqConsumeReplayRouter.resolve(RabbitMQConfig.RUSHING_ORDER_QUEUE));
    }

    @Test
    void unknownQueueCannotBeReplayed() {
        assertNull(MqConsumeReplayRouter.resolve("unknown.queue"));
    }
}
