package com.aishop.cloud.gateway.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "aishop.gateway.rate-limit")
public class GatewayRateLimitProperties {

    private boolean enabled = true;

    private double defaultQps = 200;

    private double authQps = 30;

    /** 单 IP 每秒最多调用 /api/agent/sendMessage 的次数。防止多账号分布式绕过用户级限流。 */
    private double agentSendQps = 10;

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    public double getDefaultQps() {
        return defaultQps;
    }

    public void setDefaultQps(double defaultQps) {
        this.defaultQps = defaultQps;
    }

    public double getAuthQps() {
        return authQps;
    }

    public void setAuthQps(double authQps) {
        this.authQps = authQps;
    }

    public double getAgentSendQps() {
        return agentSendQps;
    }

    public void setAgentSendQps(double agentSendQps) {
        this.agentSendQps = agentSendQps;
    }
}
