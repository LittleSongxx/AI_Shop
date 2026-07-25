package com.aishop.observability;

import io.micrometer.core.instrument.Timer;
import io.micrometer.prometheusmetrics.PrometheusConfig;
import io.micrometer.prometheusmetrics.PrometheusMeterRegistry;
import org.junit.jupiter.api.Test;

import java.time.Duration;

import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Grafana 面板里的 PromQL 写的是 Prometheus 侧的指标名，而 Micrometer 侧的名字是点号风格
 * （http.server.requests），中间要经过一次命名转换。面板一旦写错名字就是空图，
 * 这里把转换结果钉住，改 Micrometer 版本时能第一时间发现。
 */
class MetricNamingProbeTest {

    @Test
    void httpServerRequestsIsExportedWithUnderscoresAndSecondsSuffix() {
        PrometheusMeterRegistry registry = new PrometheusMeterRegistry(PrometheusConfig.DEFAULT);
        Timer.builder("http.server.requests")
                .tag("application", "aishop-order")
                .tag("uri", "/api/order/list")
                .tag("status", "200")
                .tag("outcome", "SUCCESS")
                .publishPercentileHistogram()
                .register(registry)
                .record(Duration.ofMillis(120));

        String scraped = registry.scrape();

        // 面板用的就是这三个序列名
        assertTrue(scraped.contains("http_server_requests_seconds_bucket"), "缺少 histogram bucket 序列");
        assertTrue(scraped.contains("http_server_requests_seconds_count"), "缺少 count 序列");
        assertTrue(scraped.contains("http_server_requests_seconds_sum"), "缺少 sum 序列");
        // 面板按这些标签下钻
        assertTrue(scraped.contains("application=\"aishop-order\""));
        assertTrue(scraped.contains("uri=\"/api/order/list\""));
        assertTrue(scraped.contains("outcome=\"SUCCESS\""));
    }
}
