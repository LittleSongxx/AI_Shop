package com.aishop.support;

import com.aishop.constants.Constants;
import com.aishop.constants.RabbitMQConfig;

public final class MqConsumeReplayRouter {

    private MqConsumeReplayRouter() {
    }

    public record Target(String exchange, String routingKey) {
    }

    public static Target resolve(String queueName) {
        if (queueName == null) {
            return null;
        }
        return switch (queueName) {
            case RabbitMQConfig.RAG_QUEUE ->
                    new Target(RabbitMQConfig.RAG_EXCHANGE, RabbitMQConfig.RAG_QUEUE_KEY);
            case RabbitMQConfig.RAG_DEAD_QUEUE ->
                    new Target(RabbitMQConfig.RAG_EXCHANGE, RabbitMQConfig.RAG_QUEUE_KEY);
            case RabbitMQConfig.BROWSE_RECORD_QUEUE ->
                    new Target(RabbitMQConfig.BROWSE_EXCHANGE, RabbitMQConfig.BROWSE_RECORD_KEY);
            case RabbitMQConfig.SIGN_RECORD_QUEUE ->
                    new Target(RabbitMQConfig.SIGN_RECORD_EXCHANGE, RabbitMQConfig.SIGN_RECORD_KEY);
            case RabbitMQConfig.NOTIFY_QUEUE ->
                    new Target(RabbitMQConfig.NOTIFY_EXCHANGE, RabbitMQConfig.NOTIFY_KEY);
            case RabbitMQConfig.USER_GROWTH_QUEUE, RabbitMQConfig.USER_GROWTH_DEAD_QUEUE ->
                    new Target(RabbitMQConfig.USER_GROWTH_EXCHANGE, RabbitMQConfig.USER_GROWTH_KEY);
            // 支付/物流/确认：死信队列本身即业务消费队列，重放回同一路由
            case RabbitMQConfig.PAY_TIMEOUT_DEAD_QUEUE ->
                    new Target(RabbitMQConfig.PAY_EXCHANGE, RabbitMQConfig.PAY_TIMEOUT_DEAD_KEY);
            case RabbitMQConfig.PAY_LOGISTICS_DEAD_QUEUE ->
                    new Target(RabbitMQConfig.PAY_EXCHANGE, RabbitMQConfig.PAY_LOGISTICS_DEAD_KEY);
            case RabbitMQConfig.PAY_CONFIRM_DEAD_QUEUE ->
                    new Target(RabbitMQConfig.PAY_EXCHANGE, RabbitMQConfig.PAY_CONFIRM_DEAD_KEY);
            case RabbitMQConfig.RUSHING_DEAD_QUEUE ->
                    new Target(RabbitMQConfig.RUSHING_EXCHANGE, RabbitMQConfig.RUSHING_DEAD_KEY);
            case RabbitMQConfig.USER_TEMP_BAN_DEAD_QUEUE ->
                    new Target(RabbitMQConfig.USER_TEMP_BAN_EXCHANGE, RabbitMQConfig.USER_TEMP_BAN_DEAD_KEY);
            default -> null;
        };
    }

    public static boolean isConsumeFailure(String exchange) {
        return Constants.MQ_CONSUME_FAILURE_EXCHANGE.equals(exchange);
    }
}
