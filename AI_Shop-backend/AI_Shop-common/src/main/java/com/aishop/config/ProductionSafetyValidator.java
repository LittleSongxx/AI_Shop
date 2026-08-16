package com.aishop.config;

import com.aishop.utils.StringTools;
import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class ProductionSafetyValidator {

    private static final Logger log = LoggerFactory.getLogger(ProductionSafetyValidator.class);
    private static final String WEAK_INTERNAL = "your-token";
    private static final String WEAK_ADMIN_PWD = "admin123456";

    @Value("${aishop.production-ready:false}")
    private boolean productionReady;

    @Value("${aishop.internal.token:}")
    private String internalToken;

    @Value("${aishop.internal.ops-token:}")
    private String internalOpsToken;

    @Value("${admin.password:}")
    private String adminPassword;

    @Value("${aishop.dev-login-bypass:false}")
    private boolean devLoginBypass;

    @Value("${project.folder:}")
    private String projectFolder;

    @Value("${spring.datasource.username:}")
    private String databaseUsername;

    @Value("${spring.datasource.password:}")
    private String databasePassword;

    @Value("${spring.flyway.enabled:true}")
    private boolean flywayEnabled;

    @Value("${spring.flyway.user:}")
    private String flywayUsername;

    @Value("${spring.flyway.password:}")
    private String flywayPassword;

    @PostConstruct
    public void validate() {
        if (!productionReady) {
            log.warn("aishop.production-ready=false：当前为开发/联调模式，上线前请设为 true 并配置强密钥");
            return;
        }
        StringBuilder errors = new StringBuilder();
        if (StringTools.isEmpty(internalToken) || WEAK_INTERNAL.equals(internalToken)) {
            errors.append("AISHOP_INTERNAL_TOKEN 未设置或仍为开发默认值; ");
        }
        if (StringTools.isEmpty(internalOpsToken)
                || WEAK_INTERNAL.equals(internalOpsToken)
                || internalOpsToken.equals(internalToken)) {
            errors.append("AISHOP_INTERNAL_OPS_TOKEN 未设置、过弱或与内部调用令牌复用; ");
        }
        if (StringTools.isEmpty(adminPassword) || WEAK_ADMIN_PWD.equals(adminPassword)) {
            errors.append("ADMIN_PASSWORD 未设置或仍为默认 admin123456; ");
        }
        if (StringTools.isEmpty(databaseUsername)
                || "root".equalsIgnoreCase(databaseUsername)
                || StringTools.isEmpty(databasePassword)
                || "your-password".equals(databasePassword)) {
            errors.append("MYSQL_USER/MYSQL_PASSWORD 未设置或业务服务仍在使用 root/开发默认凭据; ");
        }
        if (flywayEnabled
                && (StringTools.isEmpty(flywayUsername)
                || "root".equalsIgnoreCase(flywayUsername)
                || flywayUsername.equalsIgnoreCase(databaseUsername)
                || StringTools.isEmpty(flywayPassword)
                || flywayPassword.equals(databasePassword))) {
            errors.append("FLYWAY_USER/FLYWAY_PASSWORD 未设置或与业务运行身份复用; ");
        }
        if (devLoginBypass || System.getProperty("dev") != null) {
            errors.append("禁止生产开启 aishop.dev-login-bypass 或 -Ddev; ");
        }
        if (StringTools.isEmpty(projectFolder) || projectFolder.contains("./data")) {
            log.warn("生产建议将 PROJECT_FOLDER 指向持久化磁盘目录，当前={}", projectFolder);
        }
        if (errors.length() > 0) {
            throw new IllegalStateException("生产就绪校验失败: " + errors);
        }
        log.info("生产就绪校验通过");
    }
}
