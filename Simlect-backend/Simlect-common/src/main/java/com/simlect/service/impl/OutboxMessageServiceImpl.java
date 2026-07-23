package com.simlect.service.impl;

import com.simlect.constants.ReliableMessageSender;
import com.simlect.utils.JsonUtils;
import com.simlect.entity.enums.MessageReliabilityLevelEnum;
import com.simlect.entity.enums.OutboxMessageStatusEnum;
import com.simlect.entity.po.LocalMessageOutbox;
import com.simlect.mappers.LocalMessageOutboxMapper;
import com.simlect.service.OutboxMessageService;
import com.simlect.utils.StringTools;
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
        LocalMessageOutbox existing = localMessageOutboxMapper.selectByIdempotencyKey(idempotencyKey);
        if (existing != null) {
            return existing.getId();
        }
        Date now = new Date();
        LocalMessageOutbox row = new LocalMessageOutbox();
        row.setIdempotencyKey(idempotencyKey);
        row.setExchangeName(exchange);
        row.setRoutingKey(routingKey);
        row.setPayloadJson(JsonUtils.toJson(payload));
        row.setReliabilityLevel(reliabilityLevel == null
                ? MessageReliabilityLevelEnum.STANDARD.getCode()
                : reliabilityLevel.getCode());
        row.setStatus(OutboxMessageStatusEnum.PENDING.getStatus());
        row.setRetryCount(0);
        row.setCreateTime(now);
        row.setUpdateTime(now);
        try {
            localMessageOutboxMapper.insert(row);
            return row.getId();
        } catch (DuplicateKeyException dup) {
            LocalMessageOutbox again = localMessageOutboxMapper.selectByIdempotencyKey(idempotencyKey);
            return again == null ? null : again.getId();
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
        // 避开刚写入、即将由 afterCommit 处理的记录
        Date before = new Date(System.currentTimeMillis() - 3000L);
        Date now = new Date();
        List<LocalMessageOutbox> list = localMessageOutboxMapper.selectDispatchBatch(
                OutboxMessageStatusEnum.PENDING.getStatus(),
                OutboxMessageStatusEnum.FAILED.getStatus(),
                OutboxMessageStatusEnum.SENDING.getStatus(),
                before,
                now,
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
}
