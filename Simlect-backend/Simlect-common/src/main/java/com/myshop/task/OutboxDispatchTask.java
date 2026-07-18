package com.myshop.task;

import com.myshop.component.RedisComponent;
import com.myshop.constants.Constants;
import com.myshop.service.OutboxMessageService;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

@Component
@Slf4j
@ConditionalOnProperty(name = "mq.outbox.dispatch-enabled", havingValue = "true")
@ConditionalOnProperty(name = "app.common-scheduling.enabled", havingValue = "true")
public class OutboxDispatchTask {

    @Resource
    private OutboxMessageService outboxMessageService;
    @Resource
    private RedisComponent redisComponent;

    @Value("${mq.outbox.dispatch-batch-size:30}")
    private int batchSize;

    @Value("${mq.outbox.dispatch-max-retries:10}")
    private int maxRetries;

    @Scheduled(fixedDelayString = "${mq.outbox.dispatch-interval-ms:5000}")
    public void dispatch() {
        String lockKey = Constants.REDIS_KEY_MQ_COMPENSATE + "outbox:dispatch:lock";
        if (!redisComponent.setIfAbsent(lockKey, "1", 4, TimeUnit.SECONDS)) {
            return;
        }
        try {
            int count = outboxMessageService.dispatchPendingBatch(batchSize, maxRetries);
            if (count > 0) {
                log.info("Outbox 定时投递成功 {} 条", count);
            }
        } catch (Exception e) {
            // 表不存在（非 order/admin 库）时安静跳过
            log.debug("Outbox 定时投递跳过/失败: {}", e.getMessage());
        }
    }
}
