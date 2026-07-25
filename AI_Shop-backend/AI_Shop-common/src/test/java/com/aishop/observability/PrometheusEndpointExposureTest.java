package com.aishop.observability;

import io.micrometer.core.instrument.Counter;
import io.micrometer.prometheusmetrics.PrometheusMeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.boot.actuate.autoconfigure.metrics.CompositeMeterRegistryAutoConfiguration;
import org.springframework.boot.actuate.autoconfigure.metrics.JvmMetricsAutoConfiguration;
import org.springframework.boot.actuate.autoconfigure.metrics.MetricsAutoConfiguration;
import org.springframework.boot.actuate.autoconfigure.metrics.SystemMetricsAutoConfiguration;
import org.springframework.boot.actuate.autoconfigure.metrics.export.prometheus.PrometheusMetricsExportAutoConfiguration;
import org.springframework.boot.actuate.metrics.export.prometheus.PrometheusOutputFormat;
import org.springframework.boot.actuate.metrics.export.prometheus.PrometheusScrapeEndpoint;
import org.springframework.boot.autoconfigure.AutoConfigurations;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

import java.nio.charset.StandardCharsets;
import java.util.Properties;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * aishop-common.yml 把 prometheus 加进了 management.endpoints.web.exposure.include，
 * 但只改配置不引 micrometer-registry-prometheus 的话，/actuator/prometheus 依然是 404。
 * 这里确认注册表确实被自动配置装配上，并真实抓取一次，确认输出是 Prometheus 能解析的文本格式。
 */
class PrometheusEndpointExposureTest {

    private final ApplicationContextRunner runner = new ApplicationContextRunner()
            .withConfiguration(AutoConfigurations.of(
                    MetricsAutoConfiguration.class,
                    CompositeMeterRegistryAutoConfiguration.class,
                    JvmMetricsAutoConfiguration.class,
                    SystemMetricsAutoConfiguration.class,
                    PrometheusMetricsExportAutoConfiguration.class))
            .withPropertyValues("management.endpoints.web.exposure.include=health,info,prometheus");

    @Test
    void prometheusRegistryIsAutoConfigured() {
        // 少了 micrometer-registry-prometheus 这个 bean 就不存在，端点也就无从暴露
        runner.run(context -> assertEquals(1, context.getBeansOfType(PrometheusMeterRegistry.class).size()));
    }

    @Test
    void scrapeProducesPrometheusTextFormat() {
        runner.run(context -> {
            PrometheusMeterRegistry registry = context.getBean(PrometheusMeterRegistry.class);
            Counter.builder("aishop_probe_total")
                    .tag("application", "aishop-test")
                    .register(registry)
                    .increment();

            String body = scrape(registry);

            // 指标名、TYPE 元数据、标签三者都在，Prometheus 才能正确解析这份输出
            assertTrue(body.contains("aishop_probe_total"), "抓取结果里没有刚注册的指标");
            assertTrue(body.contains("# TYPE"), "缺少 TYPE 元数据行");
            assertTrue(body.contains("application=\"aishop-test\""), "公共标签没带上");
        });
    }

    @Test
    void jvmMetricsAreExposedForDashboardPanels() {
        // Grafana 面板里的 JVM 曲线依赖这些内置指标
        runner.run(context -> {
            PrometheusMeterRegistry registry = context.getBean(PrometheusMeterRegistry.class);
            String body = scrape(registry);
            assertTrue(body.contains("jvm_memory_used_bytes"), "缺少 JVM 内存指标");
            assertTrue(body.contains("jvm_threads_live_threads"), "缺少 JVM 线程指标");
        });
    }

    /**
     * 直接构造端点而不是从容器里取：{@code @ConditionalOnAvailableEndpoint} 需要一个真实的 web
     * 暴露技术，而 ApplicationContextRunner 起的是非 web 上下文。这里关心的是抓取输出本身，
     * 端点读的注册表和线上是同一个。
     */
    private static String scrape(PrometheusMeterRegistry registry) {
        PrometheusScrapeEndpoint endpoint =
                new PrometheusScrapeEndpoint(registry.getPrometheusRegistry(), new Properties());
        return new String(
                endpoint.scrape(PrometheusOutputFormat.CONTENT_TYPE_004, null).getBody(),
                StandardCharsets.UTF_8);
    }
}
