package com.aishop.task;

import com.aishop.component.RedisComponent;
import com.aishop.constants.Constants;
import com.aishop.service.OutboxMessageService;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.TimeUnit;

@Component
@Slf4j
@ConditionalOnProperty(name = "mq.outbox.dispatch-enabled", havingValue = "true")
public class OutboxDispatchTask {

    @Resource
    private OutboxMessageService outboxMessageService;
    @Resource
    private RedisComponent redisComponent;

    @Value("${mq.outbox.dispatch-batch-size:30}")
    private int batchSize;

    @Value("${mq.outbox.dispatch-max-retries:10}")
    private int maxRetries;

    @Value("${spring.application.name:unknown}")
    private String applicationName;

    @Autowired(required = false)
    private MeterRegistry meterRegistry;

    private final AtomicInteger exhaustedCount = new AtomicInteger();

    @PostConstruct
    void registerMetrics() {
        if (meterRegistry == null) {
            return;
        }
        Gauge.builder("aishop.mq.outbox.exhausted", exhaustedCount, AtomicInteger::get)
                .description("Current number of outbox messages requiring manual replay")
                .tag("application", applicationName)
                .register(meterRegistry);
    }

    @Scheduled(fixedDelayString = "${mq.outbox.dispatch-interval-ms:5000}")
    public void dispatch() {
        refreshExhaustedGauge();
        String lockKey = Constants.REDIS_KEY_MQ_COMPENSATE
                + "outbox:dispatch:lock:" + applicationName;
        if (!redisComponent.setIfAbsent(lockKey, "1", 4, TimeUnit.SECONDS)) {
            return;
        }
        try {
            int count = outboxMessageService.dispatchPendingBatch(batchSize, maxRetries);
            if (count > 0) {
                log.info("Outbox 定时投递成功 {} 条", count);
            }
            refreshExhaustedGauge();
        } catch (Exception e) {
            log.error("Outbox 定时投递失败", e);
        }
    }

    private void refreshExhaustedGauge() {
        try {
            exhaustedCount.set(outboxMessageService.countExhausted());
        } catch (Exception e) {
            log.warn("Outbox EXHAUSTED 指标刷新失败 application={}", applicationName, e);
        }
    }
}
