package com.aishop.security;

import com.aishop.constants.InternalApiHeaders;
import com.aishop.entity.dto.AdminPrincipalDTO;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Clock;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

@Component
public class AdminAssertionSigner {

    private final String currentSecret;
    private final String currentKeyId;
    private final Clock clock;

    public AdminAssertionSigner(
            @Value("${aishop.admin-assertion.current-secret:${aishop.internal.token:your-token}}")
            String currentSecret,
            @Value("${aishop.admin-assertion.current-key-id:current}") String currentKeyId) {
        this(currentSecret, currentKeyId, Clock.systemUTC());
    }

    AdminAssertionSigner(String currentSecret, String currentKeyId, Clock clock) {
        this.currentSecret = currentSecret;
        this.currentKeyId = currentKeyId;
        this.clock = clock;
    }

    public Map<String, String> sign(
            String method, String path, byte[] body, AdminPrincipalDTO principal) {
        if (principal == null || principal.getAdminId() == null) {
            throw new IllegalArgumentException("管理员主体不能为空");
        }
        String roles = String.join(",", principal.getRoles().stream().sorted().toList());
        String permissions = String.join(",", principal.getPermissions().stream().sorted().toList());
        String timestamp = String.valueOf(clock.instant().getEpochSecond());
        String nonce = UUID.randomUUID().toString().replace("-", "");
        String bodyHash = sha256(body == null ? new byte[0] : body);
        String account = principal.getAccount() == null ? "" : principal.getAccount();
        String canonical = canonical(
                method, path, bodyHash, principal.getAdminId(), account,
                roles, permissions, timestamp, nonce);

        Map<String, String> headers = new LinkedHashMap<>();
        headers.put(InternalApiHeaders.ADMIN_ID, principal.getAdminId());
        headers.put(InternalApiHeaders.ADMIN_ACCOUNT, account);
        headers.put(InternalApiHeaders.ADMIN_ROLES, roles);
        headers.put(InternalApiHeaders.ADMIN_PERMISSIONS, permissions);
        headers.put(InternalApiHeaders.ADMIN_TIMESTAMP, timestamp);
        headers.put(InternalApiHeaders.ADMIN_NONCE, nonce);
        headers.put(InternalApiHeaders.ADMIN_BODY_SHA256, bodyHash);
        headers.put(InternalApiHeaders.ADMIN_KEY_ID, currentKeyId);
        headers.put(InternalApiHeaders.ADMIN_SIGNATURE, hmac(canonical, currentSecret));
        return headers;
    }

    public static String canonical(
            String method,
            String path,
            String bodyHash,
            String adminId,
            String account,
            String roles,
            String permissions,
            String timestamp,
            String nonce) {
        return String.join("\n",
                method == null ? "" : method.toUpperCase(),
                path == null ? "" : path,
                bodyHash == null ? "" : bodyHash,
                adminId == null ? "" : adminId,
                account == null ? "" : account,
                roles == null ? "" : roles,
                permissions == null ? "" : permissions,
                timestamp == null ? "" : timestamp,
                nonce == null ? "" : nonce);
    }

    private static String sha256(byte[] body) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(body));
        } catch (Exception e) {
            throw new IllegalStateException("无法计算管理断言请求摘要", e);
        }
    }

    private static String hmac(String canonical, String secret) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            return HexFormat.of().formatHex(
                    mac.doFinal(canonical.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            throw new IllegalStateException("无法生成管理断言签名", e);
        }
    }
}
