package com.aishop.biz;

import com.aishop.api.dto.IdempotencyReplayAware;
import com.aishop.entity.po.OrderRequestIdempotency;
import com.aishop.exception.HttpBusinessException;
import com.aishop.mappers.OrderRequestIdempotencyMapper;
import com.aishop.utils.JsonUtils;
import com.aishop.utils.RequestFingerprint;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Service;

import java.util.function.Supplier;
import java.util.regex.Pattern;

@Service
public class OrderRequestIdempotencyService {

    public static final String COMMAND_POST_ORDER = "POST_ORDER";
    public static final String COMMAND_COUPON_RUSH_PREPARE = "COUPON_RUSH_PREPARE";
    public static final String COMMAND_COUPON_RUSH_PAY = "COUPON_RUSH_PAY";

    private static final Pattern KEY_PATTERN = Pattern.compile("[A-Za-z0-9._:-]{16,64}");

    @Resource
    private OrderRequestIdempotencyMapper mapper;

    public <T> T execute(
            String userId,
            String commandType,
            String idempotencyKey,
            Object request,
            Class<T> responseType,
            Supplier<T> command) {
        validateKey(idempotencyKey);
        String requestHash = RequestFingerprint.sha256(request);
        OrderRequestIdempotency newRecord = new OrderRequestIdempotency();
        newRecord.setUserId(userId);
        newRecord.setCommandType(commandType);
        newRecord.setIdempotencyKey(idempotencyKey);
        newRecord.setRequestHash(requestHash);

        if (mapper.insertProcessing(newRecord) == 0) {
            OrderRequestIdempotency existing = mapper.selectForUpdate(
                    userId, commandType, idempotencyKey);
            if (existing == null) {
                throw new HttpBusinessException(409, "请求正在处理中，请稍后重试");
            }
            if (!requestHash.equals(existing.getRequestHash())) {
                throw new HttpBusinessException(409, "同一幂等键不能用于不同请求");
            }
            if (!"COMPLETED".equals(existing.getStatus())
                    || existing.getResponseJson() == null
                    || existing.getResponseJson().isBlank()) {
                throw new HttpBusinessException(409, "请求正在处理中，请稍后重试");
            }
            T replay = JsonUtils.parseObject(existing.getResponseJson(), responseType);
            markReplay(replay, true);
            return replay;
        }

        T response = command.get();
        markReplay(response, false);
        int updated = mapper.markCompleted(
                userId,
                commandType,
                idempotencyKey,
                JsonUtils.toJson(response));
        if (updated != 1) {
            throw new IllegalStateException("failed to persist idempotent command response");
        }
        return response;
    }

    public void validateKey(String idempotencyKey) {
        if (idempotencyKey == null || !KEY_PATTERN.matcher(idempotencyKey).matches()) {
            throw new HttpBusinessException(
                    400,
                    "Idempotency-Key 必须为 16-64 位 ASCII 字母、数字或 . _ : -");
        }
    }

    private static void markReplay(Object response, boolean replayed) {
        if (response instanceof IdempotencyReplayAware replayAware) {
            replayAware.setIdempotencyReplayed(replayed);
        }
    }
}
