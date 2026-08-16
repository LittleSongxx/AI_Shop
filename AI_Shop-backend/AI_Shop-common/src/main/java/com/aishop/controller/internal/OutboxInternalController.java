package com.aishop.controller.internal;

import com.aishop.constants.InternalApiHeaders;
import com.aishop.controller.ABaseController;
import com.aishop.entity.vo.OutboxMessageSummaryVO;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.service.OutboxMessageService;
import jakarta.annotation.Resource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;

@RestController
@RequestMapping("/internal/mq/outbox")
@ConditionalOnProperty(name = "mq.outbox.dispatch-enabled", havingValue = "true")
public class OutboxInternalController extends ABaseController {

    @Resource
    private OutboxMessageService outboxMessageService;

    @Value("${aishop.internal.ops-token:}")
    private String expectedOpsToken;

    @GetMapping("/exhausted")
    public ResponseVO<List<OutboxMessageSummaryVO>> exhausted(
            @RequestHeader(value = InternalApiHeaders.INTERNAL_OPS_TOKEN, required = false)
            String opsToken,
            @RequestParam(defaultValue = "50") int limit) {
        requireOpsToken(opsToken);
        List<OutboxMessageSummaryVO> rows = outboxMessageService.listExhausted(limit).stream()
                .map(OutboxMessageSummaryVO::from)
                .toList();
        return getSuccessResponseVO(rows);
    }

    @PostMapping("/{id}/replay")
    public ResponseVO<Boolean> replay(
            @PathVariable Long id,
            @RequestHeader(value = InternalApiHeaders.INTERNAL_OPS_TOKEN, required = false)
            String opsToken) {
        requireOpsToken(opsToken);
        return getSuccessResponseVO(outboxMessageService.replayExhausted(id));
    }

    private void requireOpsToken(String providedToken) {
        if (expectedOpsToken == null || expectedOpsToken.isBlank()
                || providedToken == null || providedToken.isBlank()
                || !MessageDigest.isEqual(
                expectedOpsToken.getBytes(StandardCharsets.UTF_8),
                providedToken.getBytes(StandardCharsets.UTF_8))) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "运维令牌无效");
        }
    }
}
