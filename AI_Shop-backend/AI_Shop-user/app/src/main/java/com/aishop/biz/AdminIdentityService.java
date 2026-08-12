package com.aishop.biz;

import com.aishop.component.RedisComponent;
import com.aishop.constants.AdminPermissions;
import com.aishop.entity.config.AppConfig;
import com.aishop.entity.dto.AdminPrincipalDTO;
import com.aishop.exception.BusinessException;
import com.aishop.service.PasswordService;
import com.aishop.utils.StringTools;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PostConstruct;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.sql.PreparedStatement;
import java.sql.Statement;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

@Service
public class AdminIdentityService {

    private final JdbcTemplate jdbcTemplate;
    private final AppConfig appConfig;
    private final PasswordService passwordService;
    private final RedisComponent redisComponent;
    private final ObjectMapper objectMapper;

    public AdminIdentityService(
            JdbcTemplate jdbcTemplate,
            AppConfig appConfig,
            PasswordService passwordService,
            RedisComponent redisComponent,
            ObjectMapper objectMapper) {
        this.jdbcTemplate = jdbcTemplate;
        this.appConfig = appConfig;
        this.passwordService = passwordService;
        this.redisComponent = redisComponent;
        this.objectMapper = objectMapper;
    }

    @PostConstruct
    public void migrateConfiguredAdministrator() {
        String account = appConfig.getAdminAccount();
        if (StringTools.isEmpty(account)) {
            return;
        }
        jdbcTemplate.update(
                """
                INSERT INTO admin_account
                    (account, password_hash, display_name, status, session_version,
                     migrated_from_config, created_at, updated_at)
                VALUES (?, ?, ?, 1, 1, 1, NOW(3), NOW(3))
                ON DUPLICATE KEY UPDATE
                    password_hash = IF(migrated_from_config = 1, VALUES(password_hash), password_hash),
                    migrated_from_config = 1
                """,
                account.trim(), appConfig.getAdminPasswordHash(), account.trim());
        Long adminId = jdbcTemplate.queryForObject(
                "SELECT admin_id FROM admin_account WHERE account = ?", Long.class, account.trim());
        jdbcTemplate.update(
                """
                INSERT IGNORE INTO admin_account_role (admin_id, role_id)
                SELECT ?, role_id FROM admin_role WHERE role_code = ?
                """,
                adminId, AdminPermissions.SUPER_ADMIN_ROLE);
    }

    public AdminPrincipalDTO authenticate(String account, String rawPassword) {
        migrateConfiguredAdministrator();
        Map<String, Object> row = findAccountRow(account);
        if (row == null || number(row.get("status")) != 1
                || !passwordService.matches(rawPassword, String.valueOf(row.get("password_hash")))) {
            throw new BusinessException("账号或密码错误！");
        }
        jdbcTemplate.update(
                "UPDATE admin_account SET last_login_at = NOW(3) WHERE admin_id = ?",
                number(row.get("admin_id")));
        return principal(number(row.get("admin_id")));
    }

    public AdminPrincipalDTO principal(long adminId) {
        Map<String, Object> row;
        try {
            row = jdbcTemplate.queryForMap(
                    """
                    SELECT admin_id, account, display_name, status, session_version
                    FROM admin_account WHERE admin_id = ?
                    """,
                    adminId);
        } catch (EmptyResultDataAccessException e) {
            throw new BusinessException("管理员不存在");
        }
        if (number(row.get("status")) != 1) {
            throw new BusinessException("管理员已停用");
        }
        AdminPrincipalDTO principal = new AdminPrincipalDTO();
        principal.setAdminId(String.valueOf(adminId));
        principal.setAccount(String.valueOf(row.get("account")));
        Object displayName = row.get("display_name");
        principal.setDisplayName(displayName == null ? null : String.valueOf(displayName));
        principal.setSessionVersion(number(row.get("session_version")));
        principal.setRoles(jdbcTemplate.queryForList(
                """
                SELECT r.role_code FROM admin_role r
                JOIN admin_account_role ar ON ar.role_id = r.role_id
                WHERE ar.admin_id = ? ORDER BY r.role_code
                """,
                String.class, adminId));
        principal.setPermissions(jdbcTemplate.queryForList(
                """
                SELECT DISTINCT p.permission_code FROM admin_permission p
                JOIN admin_role_permission rp ON rp.permission_id = p.permission_id
                JOIN admin_account_role ar ON ar.role_id = rp.role_id
                WHERE ar.admin_id = ? ORDER BY p.permission_code
                """,
                String.class, adminId));
        return principal;
    }

    public List<Map<String, Object>> listAdministrators() {
        List<Map<String, Object>> rows = jdbcTemplate.queryForList(
                """
                SELECT admin_id, account, display_name, status, session_version,
                       last_login_at, created_at, updated_at
                FROM admin_account ORDER BY admin_id
                """);
        for (Map<String, Object> row : rows) {
            long adminId = number(row.get("admin_id"));
            row.put("roles", jdbcTemplate.queryForList(
                    """
                    SELECT r.role_code FROM admin_role r
                    JOIN admin_account_role ar ON ar.role_id = r.role_id
                    WHERE ar.admin_id = ? ORDER BY r.role_code
                    """,
                    String.class, adminId));
        }
        return rows;
    }

    public List<Map<String, Object>> listRoles() {
        List<Map<String, Object>> roles = jdbcTemplate.queryForList(
                "SELECT role_code, role_name, description FROM admin_role ORDER BY role_id");
        for (Map<String, Object> role : roles) {
            role.put("permissions", jdbcTemplate.queryForList(
                    """
                    SELECT p.permission_code FROM admin_permission p
                    JOIN admin_role_permission rp ON rp.permission_id = p.permission_id
                    JOIN admin_role r ON r.role_id = rp.role_id
                    WHERE r.role_code = ? ORDER BY p.permission_code
                    """,
                    String.class, role.get("role_code")));
        }
        return roles;
    }

    @Transactional
    public AdminPrincipalDTO createAdministrator(
            long actorAdminId,
            String account,
            String rawPassword,
            String displayName,
            Set<String> roleCodes) {
        String normalizedAccount = requireAccount(account);
        if (rawPassword == null || rawPassword.length() < 10 || rawPassword.length() > 100) {
            throw new BusinessException("管理员密码长度必须为10到100位");
        }
        Set<String> normalizedRoles = validateRoles(roleCodes);
        KeyHolder keyHolder = new GeneratedKeyHolder();
        try {
            jdbcTemplate.update(connection -> {
                PreparedStatement statement = connection.prepareStatement(
                        """
                        INSERT INTO admin_account
                            (account, password_hash, display_name, status, session_version,
                             migrated_from_config, created_at, updated_at)
                        VALUES (?, ?, ?, 1, 1, 0, NOW(3), NOW(3))
                        """,
                        Statement.RETURN_GENERATED_KEYS);
                statement.setString(1, normalizedAccount);
                statement.setString(2, passwordService.encode(rawPassword));
                statement.setString(3, textOrNull(displayName));
                return statement;
            }, keyHolder);
        } catch (DuplicateKeyException e) {
            throw new BusinessException("管理员账号已存在");
        }
        Number generatedKey = keyHolder.getKey();
        long generatedId = generatedKey == null ? 0 : generatedKey.longValue();
        if (generatedId <= 0) {
            throw new BusinessException("创建管理员失败");
        }
        replaceRoles(generatedId, normalizedRoles);
        audit(actorAdminId, "ADMIN_CREATE", generatedId, Map.of("roles", normalizedRoles));
        return principal(generatedId);
    }

    @Transactional
    public AdminPrincipalDTO updateRoles(long actorAdminId, long targetAdminId, Set<String> roleCodes) {
        Set<String> normalizedRoles = validateRoles(roleCodes);
        ensureAdministratorExists(targetAdminId);
        if (actorAdminId == targetAdminId
                && !normalizedRoles.contains(AdminPermissions.SUPER_ADMIN_ROLE)
                && countActiveSuperAdministrators() <= 1) {
            throw new BusinessException("不能移除最后一名启用中的超级管理员");
        }
        replaceRoles(targetAdminId, normalizedRoles);
        long version = bumpSessionVersion(targetAdminId);
        audit(actorAdminId, "ADMIN_ROLES_UPDATE", targetAdminId, Map.of("roles", normalizedRoles));
        redisComponent.invalidateAdminSessions(String.valueOf(targetAdminId), version);
        return principal(targetAdminId);
    }

    @Transactional
    public void updateStatus(long actorAdminId, long targetAdminId, boolean enabled) {
        ensureAdministratorExists(targetAdminId);
        if (!enabled && isActiveSuperAdministrator(targetAdminId)
                && countActiveSuperAdministrators() <= 1) {
            throw new BusinessException("不能停用最后一名超级管理员");
        }
        jdbcTemplate.update(
                "UPDATE admin_account SET status = ?, session_version = session_version + 1 WHERE admin_id = ?",
                enabled ? 1 : 0, targetAdminId);
        long version = jdbcTemplate.queryForObject(
                "SELECT session_version FROM admin_account WHERE admin_id = ?",
                Long.class, targetAdminId);
        audit(actorAdminId, "ADMIN_STATUS_UPDATE", targetAdminId, Map.of("enabled", enabled));
        redisComponent.invalidateAdminSessions(String.valueOf(targetAdminId), version);
    }

    private Map<String, Object> findAccountRow(String account) {
        if (StringTools.isEmpty(account)) {
            return null;
        }
        try {
            return jdbcTemplate.queryForMap(
                    """
                    SELECT admin_id, account, password_hash, display_name, status, session_version
                    FROM admin_account WHERE account = ?
                    """,
                    account.trim());
        } catch (EmptyResultDataAccessException e) {
            return null;
        }
    }

    private void replaceRoles(long adminId, Set<String> roles) {
        jdbcTemplate.update("DELETE FROM admin_account_role WHERE admin_id = ?", adminId);
        for (String role : roles) {
            jdbcTemplate.update(
                    """
                    INSERT INTO admin_account_role (admin_id, role_id)
                    SELECT ?, role_id FROM admin_role WHERE role_code = ?
                    """,
                    adminId, role);
        }
    }

    private Set<String> validateRoles(Set<String> roleCodes) {
        if (roleCodes == null || roleCodes.isEmpty()) {
            throw new BusinessException("至少需要一个管理员角色");
        }
        Set<String> normalized = new LinkedHashSet<>();
        for (String role : roleCodes) {
            if (!StringTools.isEmpty(role)) {
                normalized.add(role.trim().toUpperCase());
            }
        }
        if (normalized.isEmpty()) {
            throw new BusinessException("至少需要一个管理员角色");
        }
        List<String> known = jdbcTemplate.queryForList(
                "SELECT role_code FROM admin_role WHERE role_code IN ("
                        + String.join(",", normalized.stream().map(value -> "?").toList()) + ")",
                String.class,
                normalized.toArray());
        if (known.size() != normalized.size()) {
            throw new BusinessException("包含未知管理员角色");
        }
        return normalized;
    }

    private long bumpSessionVersion(long adminId) {
        jdbcTemplate.update(
                "UPDATE admin_account SET session_version = session_version + 1 WHERE admin_id = ?",
                adminId);
        return jdbcTemplate.queryForObject(
                "SELECT session_version FROM admin_account WHERE admin_id = ?",
                Long.class, adminId);
    }

    private void ensureAdministratorExists(long adminId) {
        Integer count = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM admin_account WHERE admin_id = ?", Integer.class, adminId);
        if (count == null || count == 0) {
            throw new BusinessException("管理员不存在");
        }
    }

    private boolean isActiveSuperAdministrator(long adminId) {
        Integer count = jdbcTemplate.queryForObject(
                """
                SELECT COUNT(*) FROM admin_account a
                JOIN admin_account_role ar ON ar.admin_id = a.admin_id
                JOIN admin_role r ON r.role_id = ar.role_id
                WHERE a.admin_id = ? AND a.status = 1 AND r.role_code = 'SUPER_ADMIN'
                """,
                Integer.class, adminId);
        return count != null && count > 0;
    }

    private int countActiveSuperAdministrators() {
        Integer count = jdbcTemplate.queryForObject(
                """
                SELECT COUNT(DISTINCT a.admin_id) FROM admin_account a
                JOIN admin_account_role ar ON ar.admin_id = a.admin_id
                JOIN admin_role r ON r.role_id = ar.role_id
                WHERE a.status = 1 AND r.role_code = 'SUPER_ADMIN'
                """,
                Integer.class);
        return count == null ? 0 : count;
    }

    private void audit(long actorAdminId, String action, long targetAdminId, Map<String, ?> detail) {
        String json;
        try {
            json = objectMapper.writeValueAsString(detail);
        } catch (JsonProcessingException e) {
            json = "{}";
        }
        jdbcTemplate.update(
                """
                INSERT INTO admin_security_audit_log
                    (actor_admin_id, action, target_admin_id, detail_json, created_at)
                VALUES (?, ?, ?, CAST(? AS JSON), NOW(3))
                """,
                actorAdminId, action, targetAdminId, json);
    }

    private String requireAccount(String account) {
        String normalized = textOrNull(account);
        if (normalized == null || normalized.length() > 100) {
            throw new BusinessException("管理员账号不能为空且不能超过100位");
        }
        return normalized;
    }

    private String textOrNull(String value) {
        return StringTools.isEmpty(value) ? null : value.trim();
    }

    private long number(Object value) {
        if (value instanceof Number number) {
            return number.longValue();
        }
        return Long.parseLong(String.valueOf(value));
    }
}
