package com.aishop.controller;

import com.aishop.annotation.GlobalInterceptor;
import com.aishop.entity.dto.PrivacyConfirmRequest;
import com.aishop.entity.dto.TokenUserInfoDTO;
import com.aishop.entity.enums.ResponseCodeEnum;
import com.aishop.entity.po.UserInfo;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.exception.BusinessException;
import com.aishop.integration.PrivacyAgentClient;
import com.aishop.service.PasswordService;
import com.aishop.biz.UserInfoService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.http.CacheControl;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/user/privacy")
@Validated
public class UserPrivacyController extends ABaseController {

    private final PrivacyAgentClient privacyAgentClient;
    private final UserInfoService userInfoService;
    private final PasswordService passwordService;

    public UserPrivacyController(
            PrivacyAgentClient privacyAgentClient,
            UserInfoService userInfoService,
            PasswordService passwordService) {
        this.privacyAgentClient = privacyAgentClient;
        this.userInfoService = userInfoService;
        this.passwordService = passwordService;
    }

    @PostMapping("/exports")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO<Object> createExport(
            @RequestHeader("Idempotency-Key")
            @NotBlank @Size(max = 128) String idempotencyKey,
            @Valid @RequestBody PrivacyConfirmRequest request) {
        String userId = confirmCurrentUser(request.password());
        return getSuccessResponseVO(
                privacyAgentClient.createJob(userId, "EXPORT", idempotencyKey.trim()));
    }

    @PostMapping("/deletions")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO<Object> createDeletion(
            @RequestHeader("Idempotency-Key")
            @NotBlank @Size(max = 128) String idempotencyKey,
            @Valid @RequestBody PrivacyConfirmRequest request) {
        String userId = confirmCurrentUser(request.password());
        return getSuccessResponseVO(
                privacyAgentClient.createJob(userId, "DELETE", idempotencyKey.trim()));
    }

    @GetMapping("/jobs")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO<Object> listJobs(
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int limit) {
        return getSuccessResponseVO(privacyAgentClient.listJobs(currentUserId(), limit));
    }

    @GetMapping("/jobs/{jobId}")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO<Object> getJob(
            @PathVariable @NotBlank @Size(max = 64) String jobId) {
        return getSuccessResponseVO(privacyAgentClient.getJob(currentUserId(), jobId));
    }

    @PostMapping("/jobs/{jobId}/retry")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO<Object> retryJob(
            @PathVariable @NotBlank @Size(max = 64) String jobId) {
        return getSuccessResponseVO(privacyAgentClient.retryJob(currentUserId(), jobId));
    }

    @GetMapping("/exports/{jobId}/download")
    @GlobalInterceptor(checkLogin = true)
    public ResponseEntity<byte[]> downloadExport(
            @PathVariable @NotBlank @Size(max = 64) String jobId) {
        byte[] content = privacyAgentClient.downloadExport(currentUserId(), jobId);
        ContentDisposition disposition = ContentDisposition.attachment()
                .filename("ai-data-export-" + jobId + ".json")
                .build();
        return ResponseEntity.ok()
                .contentType(MediaType.APPLICATION_JSON)
                .cacheControl(CacheControl.noStore())
                .header(HttpHeaders.CONTENT_DISPOSITION, disposition.toString())
                .body(content);
    }

    private String confirmCurrentUser(String rawPassword) {
        String userId = currentUserId();
        UserInfo userInfo = userInfoService.getUserInfoByUserId(userId);
        if (userInfo == null || !passwordService.matches(rawPassword, userInfo.getPassword())) {
            throw new BusinessException(ResponseCodeEnum.CODE_403.getCode(), "密码确认失败");
        }
        return userId;
    }

    private String currentUserId() {
        TokenUserInfoDTO principal = getTokenUserInfo();
        if (principal == null || principal.getUserId() == null || principal.getUserId().isBlank()) {
            throw new BusinessException(ResponseCodeEnum.CODE_901);
        }
        return principal.getUserId();
    }
}
