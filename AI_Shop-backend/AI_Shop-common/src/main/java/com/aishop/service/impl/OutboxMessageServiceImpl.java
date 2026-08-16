package com.aishop.service.impl;

import com.aishop.constants.ReliableMessageSender;
import com.aishop.utils.JsonUtils;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.enums.OutboxMessageStatusEnum;
import com.aishop.entity.po.LocalMessageOutbox;
import com.aishop.mappers.LocalMessageOutboxMapper;
import com.aishop.service.OutboxMessageService;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.annotation.Lazy;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.Date;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;

@Service
@Slf4j
public class OutboxMessageServiceImpl implements OutboxMessageService {

    @Resource
    private LocalMessageOutboxMapper localMessageOutboxMapper;
    @Lazy
    @Resource
    private ReliableMessageSender reliableMessageSender;

    @Value("${mq.outbox.lease-ms:30000}")
    private long leaseMs;

    @Value("${mq.outbox.retry-base-ms:2000}")
    private long retryBaseMs;

    private final String dispatcherId = UUID.randomUUID().toString();

    @Override
    public Long savePending(String exchange, String routingKey, Object payload,
                            String idempotencyKey, MessageReliabilityLevelEnum reliabilityLevel) {
        if (StringTools.isEmpty(idempotencyKey) || StringTools.isEmpty(exchange) || StringTools.isEmpty(routingKey)) {
            throw new IllegalArgumentException("outbox 参数不完整");
        }
        String payloadJson = JsonUtils.toJson(payload);
        String reliabilityCode = (reliabilityLevel == null
                ? MessageReliabilityLevelEnum.STANDARD
                : reliabilityLevel).getCode();
        LocalMessageOutbox existing = localMessageOutboxMapper.selectByIdempotencyKey(idempotencyKey);
        if (existing != null) {
            assertSameMessage(existing, exchange, routingKey, payloadJson, reliabilityCode);
            return existing.getId();
        }
        Date now = new Date();
        LocalMessageOutbox row = new LocalMessageOutbox();
        row.setIdempotencyKey(idempotencyKey);
        row.setExchangeName(exchange);
        row.setRoutingKey(routingKey);
        row.setPayloadJson(payloadJson);
        row.setReliabilityLevel(reliabilityCode);
        row.setStatus(OutboxMessageStatusEnum.PENDING.getStatus());
        row.setRetryCount(0);
        row.setCreateTime(now);
        row.setUpdateTime(now);
        try {
            localMessageOutboxMapper.insert(row);
            return row.getId();
        } catch (DuplicateKeyException dup) {
            LocalMessageOutbox again = localMessageOutboxMapper.selectByIdempotencyKey(idempotencyKey);
            if (again == null) {
                return null;
            }
            assertSameMessage(again, exchange, routingKey, payloadJson, reliabilityCode);
            return again.getId();
        }
    }

    @Override
    @Transactional(propagation = Propagation.NOT_SUPPORTED)
    public void tryDispatch(Long id) {
        if (id == null) {
            return;
        }
        LocalMessageOutbox beforeClaim = localMessageOutboxMapper.selectById(id);
        if (beforeClaim == null) {
            return;
        }
        if (OutboxMessageStatusEnum.SENT.getStatus().equals(beforeClaim.getStatus())) {
            return;
        }
        Date now = new Date();
        Date leaseUntil = new Date(now.getTime() + Math.max(leaseMs, 5000L));
        Integer claimed = localMessageOutboxMapper.claimForDispatch(
                id,
                OutboxMessageStatusEnum.PENDING.getStatus(),
                OutboxMessageStatusEnum.FAILED.getStatus(),
                OutboxMessageStatusEnum.SENDING.getStatus(),
                dispatcherId,
                leaseUntil,
                now);
        if (claimed == null || claimed != 1) {
            return;
        }
        LocalMessageOutbox row = localMessageOutboxMapper.selectById(id);
        if (row == null) {
            return;
        }
        try {
            Object payload = JsonUtils.parse(row.getPayloadJson());
            reliableMessageSender.replaySend(
                    row.getExchangeName(), row.getRoutingKey(), payload, row.getIdempotencyKey());
            Integer marked = localMessageOutboxMapper.markSent(
                    id,
                    OutboxMessageStatusEnum.SENDING.getStatus(),
                    OutboxMessageStatusEnum.SENT.getStatus(),
                    dispatcherId,
                    new Date());
            if (marked == null || marked != 1) {
                throw new IllegalStateException("Outbox 发送成功但租约已丢失, id=" + id);
            }
        } catch (Exception e) {
            log.error("Outbox 投递失败 id={}, key={}", id, row.getIdempotencyKey(), e);
            localMessageOutboxMapper.markFailed(
                    id,
                    OutboxMessageStatusEnum.SENDING.getStatus(),
                    OutboxMessageStatusEnum.FAILED.getStatus(),
                    dispatcherId,
                    truncate(e.getMessage()),
                    nextRetryTime(row.getRetryCount()));
        }
    }

    @Override
    public int dispatchPendingBatch(int batchSize, int maxRetries) {
        if (batchSize <= 0) {
            batchSize = 20;
        }
        if (maxRetries <= 0) {
            maxRetries = 10;
        }
        // 避开刚写入、即将由 afterCommit 处理的记录
        Date before = new Date(System.currentTimeMillis() - 3000L);
        Date now = new Date();
        Integer exhausted = localMessageOutboxMapper.markRetriesExhausted(
                OutboxMessageStatusEnum.FAILED.getStatus(),
                OutboxMessageStatusEnum.SENDING.getStatus(),
                OutboxMessageStatusEnum.EXHAUSTED.getStatus(),
                maxRetries,
                now);
        if (exhausted != null && exhausted > 0) {
            log.error("Outbox 有 {} 条消息重试耗尽，已转入 EXHAUSTED 等待人工处理", exhausted);
        }
        List<LocalMessageOutbox> list = localMessageOutboxMapper.selectDispatchBatch(
                OutboxMessageStatusEnum.PENDING.getStatus(),
                OutboxMessageStatusEnum.FAILED.getStatus(),
                OutboxMessageStatusEnum.SENDING.getStatus(),
                before,
                now,
                maxRetries,
                batchSize);
        if (list == null || list.isEmpty()) {
            return 0;
        }
        int ok = 0;
        for (LocalMessageOutbox row : list) {
            int retry = row.getRetryCount() == null ? 0 : row.getRetryCount();
            if (retry >= maxRetries) {
                continue;
            }
            try {
                tryDispatch(row.getId());
                LocalMessageOutbox after = localMessageOutboxMapper.selectById(row.getId());
                if (after != null && OutboxMessageStatusEnum.SENT.getStatus().equals(after.getStatus())) {
                    ok++;
                }
            } catch (Exception e) {
                log.warn("Outbox 批量投递异常 id={}", row.getId(), e);
            }
        }
        return ok;
    }

    @Override
    public int countExhausted() {
        Integer count = localMessageOutboxMapper.countByStatus(
                OutboxMessageStatusEnum.EXHAUSTED.getStatus());
        return count == null ? 0 : count;
    }

    @Override
    public List<LocalMessageOutbox> listExhausted(int limit) {
        int safeLimit = Math.max(1, Math.min(limit, 100));
        List<LocalMessageOutbox> rows = localMessageOutboxMapper.selectByStatus(
                OutboxMessageStatusEnum.EXHAUSTED.getStatus(), safeLimit);
        return rows == null ? List.of() : rows;
    }

    @Override
    @Transactional(propagation = Propagation.NOT_SUPPORTED)
    public boolean replayExhausted(Long id) {
        if (id == null) {
            return false;
        }
        Integer requeued = localMessageOutboxMapper.requeueExhausted(
                id,
                OutboxMessageStatusEnum.EXHAUSTED.getStatus(),
                OutboxMessageStatusEnum.PENDING.getStatus());
        if (requeued == null || requeued != 1) {
            return false;
        }
        log.warn("Outbox 人工重放已受理 id={}", id);
        tryDispatch(id);
        return true;
    }

    private static String truncate(String msg) {
        if (msg == null) {
            return null;
        }
        return msg.length() > 500 ? msg.substring(0, 500) : msg;
    }

    private Date nextRetryTime(Integer retryCount) {
        int retry = retryCount == null ? 0 : Math.max(0, retryCount);
        int exponent = Math.min(retry, 8);
        long base = Math.max(retryBaseMs, 500L);
        long backoff = Math.min(base * (1L << exponent), 5 * 60_000L);
        long jitter = ThreadLocalRandom.current().nextLong(Math.max(1L, base));
        return new Date(System.currentTimeMillis() + backoff + jitter);
    }

    private void assertSameMessage(
            LocalMessageOutbox existing,
            String exchange,
            String routingKey,
            String payloadJson,
            String reliabilityCode) {
        boolean samePayload = Objects.equals(existing.getPayloadJson(), payloadJson);
        if (!samePayload) {
            try {
                samePayload = Objects.equals(
                        JsonUtils.parseTree(existing.getPayloadJson()),
                        JsonUtils.parseTree(payloadJson));
            } catch (RuntimeException ignored) {
                samePayload = false;
            }
        }
        if (!Objects.equals(existing.getExchangeName(), exchange)
                || !Objects.equals(existing.getRoutingKey(), routingKey)
                || !samePayload
                || !Objects.equals(existing.getReliabilityLevel(), reliabilityCode)) {
            throw new IllegalStateException(
                    "Outbox 幂等键已被不同消息占用, key=" + existing.getIdempotencyKey());
        }
    }
}
