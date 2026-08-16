package com.aishop.config;

import jakarta.annotation.PostConstruct;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

import java.net.URI;
import java.util.Arrays;

@Component
public class SearchProductionSafetyValidator {

    @Value("${aishop.production-ready:false}")
    private boolean productionReady;

    @Value("${spring.elasticsearch.uris:http://127.0.0.1:9200}")
    private String elasticsearchUris;

    @Value("${spring.elasticsearch.username:}")
    private String elasticsearchUsername;

    @Value("${spring.elasticsearch.password:}")
    private String elasticsearchPassword;

    @Value("${spring.ai.model.embedding:local}")
    private String embeddingProvider;

    @Value("${spring.ai.openai.embedding.api-key:}")
    private String embeddingApiKey;

    @Value("${spring.ai.vectorstore.elasticsearch.initialize-schema:true}")
    private boolean initializeVectorSchema;

    @PostConstruct
    public void validate() {
        if (!productionReady) {
            return;
        }
        boolean insecureEndpoint = Arrays.stream(elasticsearchUris.split(","))
                .map(String::trim)
                .filter(StringUtils::hasText)
                .map(URI::create)
                .anyMatch(uri -> !"https".equalsIgnoreCase(uri.getScheme()));
        if (insecureEndpoint) {
            throw new IllegalStateException(
                    "生产就绪校验失败: Elasticsearch 必须使用 HTTPS");
        }
        if (!StringUtils.hasText(elasticsearchUsername)
                || !StringUtils.hasText(elasticsearchPassword)) {
            throw new IllegalStateException(
                    "生产就绪校验失败: 请配置 ES_USERNAME 和 ES_PASSWORD");
        }
        if (!"openai".equalsIgnoreCase(embeddingProvider)
                || !StringUtils.hasText(embeddingApiKey)) {
            throw new IllegalStateException(
                    "生产就绪校验失败: Search 必须使用真实 Embedding Provider 和 API Key");
        }
        if (initializeVectorSchema) {
            throw new IllegalStateException(
                    "生产就绪校验失败: VECTOR_INITIALIZE_SCHEMA 必须为 false，"
                            + "请使用版本化物理索引回填并原子切换 alias");
        }
    }
}
