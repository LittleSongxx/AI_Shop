package com.aishop.cloud.gateway.config;

import com.alibaba.csp.sentinel.adapter.gateway.common.SentinelGatewayConstants;
import com.alibaba.csp.sentinel.adapter.gateway.common.api.ApiDefinition;
import com.alibaba.csp.sentinel.adapter.gateway.common.api.ApiPathPredicateItem;
import com.alibaba.csp.sentinel.adapter.gateway.common.api.ApiPredicateItem;
import com.alibaba.csp.sentinel.adapter.gateway.common.api.GatewayApiDefinitionManager;
import com.alibaba.csp.sentinel.adapter.gateway.common.rule.GatewayFlowRule;
import com.alibaba.csp.sentinel.adapter.gateway.common.rule.GatewayParamFlowItem;
import com.alibaba.csp.sentinel.adapter.gateway.common.rule.GatewayRuleManager;
import com.alibaba.csp.sentinel.adapter.gateway.sc.callback.BlockRequestHandler;
import com.alibaba.csp.sentinel.adapter.gateway.sc.callback.GatewayCallbackManager;
import com.alibaba.csp.sentinel.slots.block.RuleConstant;
import com.alibaba.csp.sentinel.slots.block.degrade.DegradeRule;
import com.alibaba.csp.sentinel.slots.block.degrade.DegradeRuleManager;
import jakarta.annotation.PostConstruct;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.reactive.function.server.ServerResponse;
import org.springframework.web.server.ServerWebExchange;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

@Configuration
public class SentinelGatewayConfig {

    private final GatewayRateLimitProperties rateLimitProperties;

    public SentinelGatewayConfig(GatewayRateLimitProperties rateLimitProperties) {
        this.rateLimitProperties = rateLimitProperties;
    }

    @PostConstruct
    public void init() {
        initBlockHandler();
        // 降级规则：Agent 直连路由没有 lb:// 保护，需要在网关层单独声明熔断，
        // 避免 Agent 服务慢/异常时持续占用网关连接池。
        initDegradeRules();
        if (!rateLimitProperties.isEnabled()) {
            return;
        }
        initCustomApis();
        initGatewayRules();
    }

    /**
     * 为 Python Agent 路由（agent-http、agent-ws）注册 Sentinel 降级规则。
     *
     * <p>其他 Java 服务路由经 {@code lb://} 通过 OpenFeign + Sentinel 保护；
     * Agent 直连 HTTP/WS，不经过 Nacos 负载均衡，因此需要在此处单独声明熔断规则，
     * 防止 Agent 出现慢响应或异常时持续占用网关连接池，引发级联失败。
     *
     * <p>策略：异常比例 &ge; 50%（至少 5 个请求）时触发熔断，休眠 10&nbsp;s 后进入半开探测。
     */
    private void initDegradeRules() {
        List<DegradeRule> rules = new ArrayList<>();

        // agent-http（REST 对话接口）
        rules.add(new DegradeRule("agent-http")
                .setGrade(RuleConstant.DEGRADE_GRADE_EXCEPTION_RATIO)
                .setCount(0.5)
                .setMinRequestAmount(5)
                .setTimeWindow(10)
                .setStatIntervalMs(10_000));

        // agent-ws（WebSocket 流式接口，与 agent-http 独立统计）
        rules.add(new DegradeRule("agent-ws")
                .setGrade(RuleConstant.DEGRADE_GRADE_EXCEPTION_RATIO)
                .setCount(0.5)
                .setMinRequestAmount(5)
                .setTimeWindow(10)
                .setStatIntervalMs(10_000));

        DegradeRuleManager.loadRules(rules);
    }

    private void initCustomApis() {
        Set<ApiDefinition> definitions = new HashSet<>();

        Set<ApiPredicateItem> authItems = new HashSet<>();
        authItems.add(exact("/api/account/login"));
        authItems.add(exact("/api/account/register"));
        authItems.add(exact("/api/account/getEmailCode"));
        authItems.add(exact("/api/account/forgetPassword"));
        authItems.add(prefix("/admin-api/account/"));
        definitions.add(new ApiDefinition("auth-sensitive").setPredicateItems(authItems));

        Set<ApiPredicateItem> webItems = new HashSet<>();
        webItems.add(prefix("/api/"));
        definitions.add(new ApiDefinition("web-api").setPredicateItems(webItems));

        Set<ApiPredicateItem> adminItems = new HashSet<>();
        adminItems.add(prefix("/admin-api/"));
        definitions.add(new ApiDefinition("admin-api").setPredicateItems(adminItems));

        // Agent 发消息接口（IP 级精细限流）：防止多账号分布式绕过用户级配额。
        // 单 IP 每秒最多 agentSendQps 次；/api/agent/sendMessage 是 LLM 开销的主要入口，
        // 其余 agent 接口走通用 web-api 规则。
        Set<ApiPredicateItem> agentSendItems = new HashSet<>();
        agentSendItems.add(exact("/api/agent/sendMessage"));
        definitions.add(new ApiDefinition("agent-send").setPredicateItems(agentSendItems));

        GatewayApiDefinitionManager.loadApiDefinitions(definitions);
    }

    private void initGatewayRules() {
        Set<GatewayFlowRule> rules = new HashSet<>();
        double defaultQps = rateLimitProperties.getDefaultQps();
        double authQps = rateLimitProperties.getAuthQps();
        double agentSendQps = rateLimitProperties.getAgentSendQps();

        rules.add(apiRule("auth-sensitive", authQps));
        rules.add(apiRule("web-api", defaultQps));
        rules.add(apiRule("admin-api", defaultQps));

        // agent-send：IP 级精细限流，防止多账号分布式绕过用户级配额。
        // 按来源 IP（PARAM_PARSE_STRATEGY_CLIENT_IP）限流，不按 API 分组。
        // 这比 web-api 规则更精细：web-api 是 API 整体 QPS，agent-send 是每 IP 的 QPS。
        GatewayFlowRule agentSendRule = new GatewayFlowRule("agent-send")
                .setResourceMode(SentinelGatewayConstants.RESOURCE_MODE_CUSTOM_API_NAME)
                .setCount(agentSendQps)
                .setIntervalSec(1)
                .setParamItem(new GatewayParamFlowItem()
                        .setParseStrategy(SentinelGatewayConstants.PARAM_PARSE_STRATEGY_CLIENT_IP));
        rules.add(agentSendRule);

        for (String routeId : new String[]{
                "user", "product", "cart", "order", "pay", "coupon",
                "search", "stock", "admin", "agent-http"
        }) {
            rules.add(new GatewayFlowRule(routeId).setCount(defaultQps).setIntervalSec(1));
        }
        GatewayRuleManager.loadRules(rules);
    }

    private void initBlockHandler() {
        BlockRequestHandler blockHandler = (ServerWebExchange exchange, Throwable ex) -> {
            Map<String, Object> body = new HashMap<>();
            body.put("status", "error");
            body.put("code", 429);
            body.put("info", "请求过于频繁，请稍后再试");
            body.put("data", null);
            return ServerResponse.status(HttpStatus.TOO_MANY_REQUESTS)
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(body);
        };
        GatewayCallbackManager.setBlockHandler(blockHandler);
    }

    private static GatewayFlowRule apiRule(String apiName, double qps) {
        return new GatewayFlowRule(apiName)
                .setResourceMode(SentinelGatewayConstants.RESOURCE_MODE_CUSTOM_API_NAME)
                .setCount(qps)
                .setIntervalSec(1);
    }

    private static ApiPathPredicateItem exact(String path) {
        return new ApiPathPredicateItem()
                .setPattern(path)
                .setMatchStrategy(SentinelGatewayConstants.URL_MATCH_STRATEGY_EXACT);
    }

    private static ApiPathPredicateItem prefix(String path) {
        return new ApiPathPredicateItem()
                .setPattern(path)
                .setMatchStrategy(SentinelGatewayConstants.URL_MATCH_STRATEGY_PREFIX);
    }
}
