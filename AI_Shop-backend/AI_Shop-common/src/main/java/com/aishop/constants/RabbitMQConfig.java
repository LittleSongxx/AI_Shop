package com.aishop.constants;

import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.Declarable;
import org.springframework.amqp.core.Declarables;
import org.springframework.amqp.core.DirectExchange;
import org.springframework.amqp.core.QueueBuilder;
import org.springframework.amqp.rabbit.config.SimpleRabbitListenerContainerFactory;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.amqp.support.converter.MessageConverter;
import org.springframework.boot.autoconfigure.amqp.SimpleRabbitListenerContainerFactoryConfigurer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.amqp.core.Queue;
import org.springframework.beans.factory.annotation.Value;

import java.util.ArrayList;
import java.util.List;

@Configuration
public class RabbitMQConfig {

    @Value("${user.temp-ban.ms:7200000}")
    private int userTempBanMs;

    @Value("${rush.expire.ms:900000}")
    private int rushExpireMs;

    @Value("${order.expire.ms:900000}")
    private int orderExpireMs;

    @Value("${logistics.simulate.interval-second:3600}")
    private int logisticsSimulateIntervalSecond;

    @Value("${order.confirm.minute:10080}")
    private int orderConfirmMinute;

    @Value("${mq.queue-type:quorum}")
    private String queueType;

    // 交换机
    public static final String RUSHING_EXCHANGE = "rushing.exchange";
    // 支付、订单相关的交换机
    public static final String PAY_EXCHANGE = "pay.exchange";
    // Rag相关的交换机
    public static final String RAG_EXCHANGE = "rag.exchange";
    // 浏览足迹交换机
    public static final String BROWSE_EXCHANGE = "browse.exchange";
    // 通知交换机
    public static final String NOTIFY_EXCHANGE = "notify.exchange";
    public static final String REFUND_EXCHANGE = "refund.exchange";
    public static final String USER_GROWTH_EXCHANGE = "user.growth.exchange";
    public static final String COMMERCE_OUTCOME_EXCHANGE = "commerce.outcome.exchange";
    public static final String MQ_RETRY_EXCHANGE = "mq.retry.exchange";
    public static final String MQ_FAILURE_EXCHANGE = "mq.failure.exchange";
    public static final String MQ_FAILURE_QUEUE = "mq.failure.queue";
    public static final String MQ_FAILURE_KEY = "mq.failure";
    private static final long[] RETRY_DELAYS_MS = {5_000L, 30_000L, 120_000L};

    // 队列
    public static final String RUSHING_ORDER_QUEUE = "rushing.order.queue";
    // Rag队列
    public static final String RAG_QUEUE = "rag.queue";
    // Rag死信队列
    public static final String RAG_DEAD_QUEUE = "rag.dead.queue";
    public static final String BROWSE_RECORD_QUEUE = "browse.record.queue";
    public static final String REFUND_STOCK_QUEUE = "refund.stock.queue";
    public static final String REFUND_STOCK_DEAD_QUEUE = "refund.stock.dead.queue";
    public static final String REFUND_RESULT_QUEUE = "refund.result.queue";
    public static final String REFUND_RESULT_DEAD_QUEUE = "refund.result.dead.queue";
    public static final String USER_GROWTH_QUEUE = "user.growth.queue";
    public static final String USER_GROWTH_DEAD_QUEUE = "user.growth.dead.queue";
    public static final String COMMERCE_OUTCOME_QUEUE = "commerce.outcome.queue";
    public static final String COMMERCE_OUTCOME_DEAD_QUEUE = "commerce.outcome.dead.queue";

    // 死信队列（超时未支付释放库存）
    public static final String RUSHING_DELAY_QUEUE = "rushing.delay.queue";
    public static final String RUSHING_DEAD_QUEUE = "rushing.dead.queue";
    // 支付超时延时队列
    public static final String PAY_TIMEOUT_DELAY_QUEUE = "pay.timeout.delay.queue";
    // 支付超时队列（死信）
    public static final String PAY_TIMEOUT_DEAD_QUEUE = "pay.timeout.dead.queue";
    // 模拟发货延时队列
    public static final String PAY_LOGISTICS_DELAY_QUEUE = "pay.logistics.delay.queue";
    // 模拟发货队列（死信）
    public static final String PAY_LOGISTICS_DEAD_QUEUE = "pay.logistics.dead.queue";
    // 自动确认收货延时队列
    public static final String PAY_CONFIRM_DELAY_QUEUE = "pay.confirm.delay.queue";
    // 自动确认收货队列（死信）
    public static final String PAY_CONFIRM_DEAD_QUEUE = "pay.confirm.dead.queue";

    // 路由键
    public static final String RUSHING_ORDER_KEY = "rushing.order";
    public static final String RUSHING_DELAY_KEY = "rushing.delay";
    public static final String RUSHING_DEAD_KEY = "rushing.dead";
    public static final String PAY_TIMEOUT_DELAY_KEY = "pay.timeout.delay";
    public static final String PAY_TIMEOUT_DEAD_KEY = "pay.timeout.dead";
    public static final String PAY_LOGISTICS_DELAY_KEY = "pay.logistics.delay";
    public static final String PAY_LOGISTICS_DEAD_KEY = "pay.logistics.dead";
    public static final String PAY_CONFIRM_DELAY_KEY = "pay.confirm.delay";
    public static final String PAY_CONFIRM_DEAD_KEY = "pay.confirm.dead";
    public static final String RAG_QUEUE_KEY = "rag.queue";
    public static final String RAG_DEAD_QUEUE_KEY = "rag.dead.queue";
    public static final String BROWSE_RECORD_KEY = "browse.record";
    public static final String REFUND_STOCK_KEY = "refund.stock";
    public static final String REFUND_STOCK_DEAD_KEY = "refund.stock.dead";
    public static final String REFUND_RESULT_KEY = "refund.result";
    public static final String REFUND_RESULT_DEAD_KEY = "refund.result.dead";
    public static final String USER_GROWTH_KEY = "user.growth";
    public static final String USER_GROWTH_DEAD_KEY = "user.growth.dead";
    public static final String COMMERCE_OUTCOME_KEY = "commerce.outcome";
    public static final String COMMERCE_OUTCOME_DEAD_KEY = "commerce.outcome.dead";

    // 通知队列
    public static final String NOTIFY_QUEUE = "notify.queue";
    // 通知路由键
    public static final String NOTIFY_KEY = "notify";

    // 签到记录交换机
    public static final String SIGN_RECORD_EXCHANGE = "sign_record.exchange";
    // 签到记录队列
    public static final String SIGN_RECORD_QUEUE = "sign_record.queue";
    // 签到记录路由键
    public static final String SIGN_RECORD_KEY = "sign_record";

    // 用户临时封禁（延时解封）
    public static final String USER_TEMP_BAN_EXCHANGE = "user.tempban.exchange";
    public static final String USER_TEMP_BAN_DELAY_QUEUE = "user.tempban.delay.queue";
    public static final String USER_TEMP_BAN_DEAD_QUEUE = "user.tempban.dead.queue";
    public static final String USER_TEMP_BAN_DELAY_KEY = "user.tempban.delay";
    public static final String USER_TEMP_BAN_DEAD_KEY = "user.tempban.dead";

    private static final List<String> RETRYABLE_QUEUES = List.of(
            RUSHING_ORDER_QUEUE,
            RUSHING_DEAD_QUEUE,
            PAY_TIMEOUT_DEAD_QUEUE,
            PAY_LOGISTICS_DEAD_QUEUE,
            PAY_CONFIRM_DEAD_QUEUE,
            RAG_QUEUE,
            BROWSE_RECORD_QUEUE,
            NOTIFY_QUEUE,
            SIGN_RECORD_QUEUE,
            REFUND_STOCK_QUEUE,
            REFUND_RESULT_QUEUE,
            USER_GROWTH_QUEUE,
            USER_TEMP_BAN_DEAD_QUEUE
    );

    public static String retryRoutingKey(String queueName, int attempt) {
        return queueName + ".retry." + attempt;
    }

    private QueueBuilder durableQueue(String name) {
        QueueBuilder builder = QueueBuilder.durable(name);
        return "quorum".equalsIgnoreCase(queueType) ? builder.quorum() : builder;
    }

    // ========== 正常订单队列 ==========

    @Bean
    public DirectExchange rushingExchange() {
        // durable=true：Broker 重启后交换机仍在
        return new DirectExchange(RUSHING_EXCHANGE, true, false);
    }

    @Bean
    public Queue rushingOrderQueue() {
        // durable 队列 + 配合消息 PERSISTENT，Broker 重启不丢消息
        return durableQueue(RUSHING_ORDER_QUEUE).build();
    }

    @Bean
    public Binding rushingOrderBinding() {
        return BindingBuilder.bind(rushingOrderQueue())
                .to(rushingExchange())
                .with(RUSHING_ORDER_KEY);
    }

    // ========== 延迟队列（1分钟超时）==========

    @Bean
    public Queue rushingDelayQueue() {
        return durableQueue(RUSHING_DELAY_QUEUE)
                .withArgument("x-message-ttl", rushExpireMs)  // 秒杀支付超时
                .withArgument("x-dead-letter-exchange", RUSHING_EXCHANGE)
                .withArgument("x-dead-letter-routing-key", RUSHING_DEAD_KEY)
                .build();
    }

    @Bean
    public Binding rushingDelayBinding() {
        return BindingBuilder.bind(rushingDelayQueue())
                .to(rushingExchange())
                .with(RUSHING_DELAY_KEY);
    }

    // ========== 死信队列（超时处理）==========

    @Bean
    public Queue rushingDeadQueue() {
        return durableQueue(RUSHING_DEAD_QUEUE).build();
    }

    @Bean
    public Binding rushingDeadBinding() {
        return BindingBuilder.bind(rushingDeadQueue())
                .to(rushingExchange())
                .with(RUSHING_DEAD_KEY);
    }

    // ========== 支付、订单相关 ==========
    // ========== 支付交换机 ==========
    @Bean
    public DirectExchange payExchange() {
        return new DirectExchange(PAY_EXCHANGE, true, false);
    }
    // ========== 支付超时队列 ==========
    @Bean
    public Queue payTimeoutDelayQueue() {
        return durableQueue(PAY_TIMEOUT_DELAY_QUEUE)
                .withArgument("x-message-ttl", orderExpireMs)  // 普通订单支付超时
                .withArgument("x-dead-letter-exchange", PAY_EXCHANGE)
                .withArgument("x-dead-letter-routing-key", PAY_TIMEOUT_DEAD_KEY)
                .build();
    }
    @Bean
    public Binding payTimeoutDelayBinding() {
        return BindingBuilder.bind(payTimeoutDelayQueue())
                .to(payExchange())
                .with(PAY_TIMEOUT_DELAY_KEY);
    }

    @Bean
    public Queue payTimeoutDeadQueue() {
        return durableQueue(PAY_TIMEOUT_DEAD_QUEUE).build();
    }

    @Bean
    public Binding payTimeoutDeadBinding() {
        return BindingBuilder.bind(payTimeoutDeadQueue())
                .to(payExchange())
                .with(PAY_TIMEOUT_DEAD_KEY);
    }
    // ========== 模拟发货队列 ==========
    @Bean
    public Queue payLogisticsDelayQueue() {
        return durableQueue(PAY_LOGISTICS_DELAY_QUEUE)
                .withArgument("x-message-ttl", logisticsSimulateIntervalSecond * 1000L)
                .withArgument("x-dead-letter-exchange", PAY_EXCHANGE)
                .withArgument("x-dead-letter-routing-key", PAY_LOGISTICS_DEAD_KEY)
                .build();
    }
    @Bean
    public Binding payLogisticsDelayBinding() {
        return BindingBuilder.bind(payLogisticsDelayQueue())
                .to(payExchange())
                .with(PAY_LOGISTICS_DELAY_KEY);
    }

    @Bean
    public Queue payLogisticsDeadQueue() {
        return durableQueue(PAY_LOGISTICS_DEAD_QUEUE).build();
    }

    @Bean
    public Binding payLogisticsDeadBinding() {
        return BindingBuilder.bind(payLogisticsDeadQueue())
                .to(payExchange())
                .with(PAY_LOGISTICS_DEAD_KEY);
    }
    // ========== 自动确认收货队列 ==========
    @Bean
    public Queue payConfirmDelayQueue() {
        return durableQueue(PAY_CONFIRM_DELAY_QUEUE)
                .withArgument("x-message-ttl", orderConfirmMinute * 60 * 1000L)
                .withArgument("x-dead-letter-exchange", PAY_EXCHANGE)
                .withArgument("x-dead-letter-routing-key", PAY_CONFIRM_DEAD_KEY)
                .build();
    }
    @Bean
    public Binding payConfirmDelayBinding() {
        return BindingBuilder.bind(payConfirmDelayQueue())
                .to(payExchange())
                .with(PAY_CONFIRM_DELAY_KEY);
    }

    @Bean
    public Queue payConfirmDeadQueue() {
        return durableQueue(PAY_CONFIRM_DEAD_QUEUE).build();
    }

    @Bean
    public Binding payConfirmDeadBinding() {
        return BindingBuilder.bind(payConfirmDeadQueue())
                .to(payExchange())
                .with(PAY_CONFIRM_DEAD_KEY);
    }

    @Bean
    public DirectExchange commerceOutcomeExchange() {
        return new DirectExchange(COMMERCE_OUTCOME_EXCHANGE, true, false);
    }

    @Bean
    public Queue commerceOutcomeQueue() {
        QueueBuilder builder = durableQueue(COMMERCE_OUTCOME_QUEUE)
                .withArgument("x-dead-letter-exchange", COMMERCE_OUTCOME_EXCHANGE)
                .withArgument("x-dead-letter-routing-key", COMMERCE_OUTCOME_DEAD_KEY);
        if ("quorum".equalsIgnoreCase(queueType)) {
            builder.withArgument("x-delivery-limit", 10);
        }
        return builder.build();
    }

    @Bean
    public Queue commerceOutcomeDeadQueue() {
        return durableQueue(COMMERCE_OUTCOME_DEAD_QUEUE).build();
    }

    @Bean
    public Binding commerceOutcomeBinding() {
        return BindingBuilder.bind(commerceOutcomeQueue())
                .to(commerceOutcomeExchange())
                .with(COMMERCE_OUTCOME_KEY);
    }

    @Bean
    public Binding commerceOutcomeDeadBinding() {
        return BindingBuilder.bind(commerceOutcomeDeadQueue())
                .to(commerceOutcomeExchange())
                .with(COMMERCE_OUTCOME_DEAD_KEY);
    }

    @Bean
    public DirectExchange refundExchange() {
        return new DirectExchange(REFUND_EXCHANGE, true, false);
    }

    @Bean
    public Queue refundStockQueue() {
        return durableQueue(REFUND_STOCK_QUEUE)
                .withArgument("x-dead-letter-exchange", REFUND_EXCHANGE)
                .withArgument("x-dead-letter-routing-key", REFUND_STOCK_DEAD_KEY)
                .build();
    }

    @Bean
    public Queue refundStockDeadQueue() {
        return durableQueue(REFUND_STOCK_DEAD_QUEUE).build();
    }

    @Bean
    public Queue refundResultQueue() {
        return durableQueue(REFUND_RESULT_QUEUE)
                .withArgument("x-dead-letter-exchange", REFUND_EXCHANGE)
                .withArgument("x-dead-letter-routing-key", REFUND_RESULT_DEAD_KEY)
                .build();
    }

    @Bean
    public Queue refundResultDeadQueue() {
        return durableQueue(REFUND_RESULT_DEAD_QUEUE).build();
    }

    @Bean
    public Binding refundStockBinding() {
        return BindingBuilder.bind(refundStockQueue()).to(refundExchange()).with(REFUND_STOCK_KEY);
    }

    @Bean
    public Binding refundStockDeadBinding() {
        return BindingBuilder.bind(refundStockDeadQueue())
                .to(refundExchange()).with(REFUND_STOCK_DEAD_KEY);
    }

    @Bean
    public Binding refundResultBinding() {
        return BindingBuilder.bind(refundResultQueue()).to(refundExchange()).with(REFUND_RESULT_KEY);
    }

    @Bean
    public Binding refundResultDeadBinding() {
        return BindingBuilder.bind(refundResultDeadQueue())
                .to(refundExchange()).with(REFUND_RESULT_DEAD_KEY);
    }

    // ========== 订单完成后会员成长值 ==========
    @Bean
    public DirectExchange userGrowthExchange() {
        return new DirectExchange(USER_GROWTH_EXCHANGE, true, false);
    }

    @Bean
    public Queue userGrowthQueue() {
        return durableQueue(USER_GROWTH_QUEUE)
                .withArgument("x-dead-letter-exchange", USER_GROWTH_EXCHANGE)
                .withArgument("x-dead-letter-routing-key", USER_GROWTH_DEAD_KEY)
                .build();
    }

    @Bean
    public Queue userGrowthDeadQueue() {
        return durableQueue(USER_GROWTH_DEAD_QUEUE).build();
    }

    @Bean
    public Binding userGrowthBinding() {
        return BindingBuilder.bind(userGrowthQueue())
                .to(userGrowthExchange()).with(USER_GROWTH_KEY);
    }

    @Bean
    public Binding userGrowthDeadBinding() {
        return BindingBuilder.bind(userGrowthDeadQueue())
                .to(userGrowthExchange()).with(USER_GROWTH_DEAD_KEY);
    }

    // ========== 浏览足迹异步落库 ==========
    @Bean
    public DirectExchange browseExchange() {
        return new DirectExchange(BROWSE_EXCHANGE, true, false);
    }

    @Bean
    public Queue browseRecordQueue() {
        return durableQueue(BROWSE_RECORD_QUEUE).build();
    }

    @Bean
    public Binding browseRecordBinding() {
        return BindingBuilder.bind(browseRecordQueue())
                .to(browseExchange())
                .with(BROWSE_RECORD_KEY);
    }

    // Rag交换机
    @Bean
    public DirectExchange ragExchange() {
        return new DirectExchange(RAG_EXCHANGE, true, false);
    }

    // Rag队列（消费失败经 DLX 进入 rag.dead.queue）
    @Bean
    public Queue ragQuestionQueue() {
        return durableQueue(RAG_QUEUE)
                .withArgument("x-dead-letter-exchange", RAG_EXCHANGE)
                .withArgument("x-dead-letter-routing-key", RAG_DEAD_QUEUE_KEY)
                .build();
    }

    @Bean
    public Binding ragQuestionBinding() {
        return BindingBuilder.bind(ragQuestionQueue())
                .to(ragExchange())
                .with(RAG_QUEUE_KEY);
    }

    // Rag死信队列
    @Bean
    public Queue ragDeadQueue() {
        return durableQueue(RAG_DEAD_QUEUE).build();
    }

    @Bean
    public Binding ragDeadBinding() {
        return BindingBuilder.bind(ragDeadQueue())
                .to(ragExchange())
                .with(RAG_DEAD_QUEUE_KEY);
    }

    // ========== 通知队列 ==========
    @Bean
    public DirectExchange notifyExchange() {
        return new DirectExchange(NOTIFY_EXCHANGE, true, false);
    }

    @Bean
    public Queue notifyQueue() {
        return durableQueue(NOTIFY_QUEUE).build();
    }

    @Bean
    public Binding notifyBinding() {
        return BindingBuilder.bind(notifyQueue())
                .to(notifyExchange())
                .with(NOTIFY_KEY);
    }

    // ========== 签到记录队列 ==========
    @Bean
    public DirectExchange signRecordExchange() {
        return new DirectExchange(SIGN_RECORD_EXCHANGE, true, false);
    }

    @Bean
    public Queue signRecordQueue() {
        return durableQueue(SIGN_RECORD_QUEUE).build();
    }

    @Bean
    public Binding signRecordBinding() {
        return BindingBuilder.bind(signRecordQueue())
                .to(signRecordExchange())
                .with(SIGN_RECORD_KEY);
    }

    // ========== 用户临时封禁（延时解封）==========
    @Bean
    public DirectExchange userTempBanExchange() {
        return new DirectExchange(USER_TEMP_BAN_EXCHANGE, true, false);
    }

    @Bean
    public Queue userTempBanDelayQueue() {
        return durableQueue(USER_TEMP_BAN_DELAY_QUEUE)
                .withArgument("x-message-ttl", userTempBanMs)
                .withArgument("x-dead-letter-exchange", USER_TEMP_BAN_EXCHANGE)
                .withArgument("x-dead-letter-routing-key", USER_TEMP_BAN_DEAD_KEY)
                .build();
    }

    @Bean
    public Queue userTempBanDeadQueue() {
        return durableQueue(USER_TEMP_BAN_DEAD_QUEUE).build();
    }

    @Bean
    public Binding userTempBanDelayBinding() {
        return BindingBuilder.bind(userTempBanDelayQueue())
                .to(userTempBanExchange())
                .with(USER_TEMP_BAN_DELAY_KEY);
    }

    @Bean
    public Binding userTempBanDeadBinding() {
        return BindingBuilder.bind(userTempBanDeadQueue())
                .to(userTempBanExchange())
                .with(USER_TEMP_BAN_DEAD_KEY);
    }

    @Bean
    public DirectExchange mqRetryExchange() {
        return new DirectExchange(MQ_RETRY_EXCHANGE, true, false);
    }

    @Bean
    public DirectExchange mqFailureExchange() {
        return new DirectExchange(MQ_FAILURE_EXCHANGE, true, false);
    }

    @Bean
    public Queue mqFailureQueue() {
        return durableQueue(MQ_FAILURE_QUEUE).build();
    }

    @Bean
    public Binding mqFailureBinding() {
        return BindingBuilder.bind(mqFailureQueue()).to(mqFailureExchange()).with(MQ_FAILURE_KEY);
    }

    @Bean
    public Declarables mqRetryTopology() {
        List<Declarable> declarables = new ArrayList<>();
        for (String queueName : RETRYABLE_QUEUES) {
            for (int i = 0; i < RETRY_DELAYS_MS.length; i++) {
                int attempt = i + 1;
                String retryQueueName = retryRoutingKey(queueName, attempt);
                Queue retryQueue = durableQueue(retryQueueName)
                        .withArgument("x-message-ttl", RETRY_DELAYS_MS[i])
                        .withArgument("x-dead-letter-exchange", "")
                        .withArgument("x-dead-letter-routing-key", queueName)
                        .build();
                declarables.add(retryQueue);
                declarables.add(BindingBuilder.bind(retryQueue)
                        .to(mqRetryExchange())
                        .with(retryRoutingKey(queueName, attempt)));
            }
        }
        return new Declarables(declarables);
    }

    @Bean
    public MessageConverter messageConverter() {
        return new Jackson2JsonMessageConverter();
    }

    @Bean
    public SimpleRabbitListenerContainerFactory rabbitListenerContainerFactory(
            ConnectionFactory connectionFactory,
            SimpleRabbitListenerContainerFactoryConfigurer configurer) {
        SimpleRabbitListenerContainerFactory factory = new SimpleRabbitListenerContainerFactory();
        configurer.configure(factory, connectionFactory);
        factory.setMessageConverter(messageConverter());
        return factory;
    }
}
