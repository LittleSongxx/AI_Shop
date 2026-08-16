package com.aishop.component;

import org.springframework.ai.embedding.EmbeddingModel;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.actuate.health.Health;
import org.springframework.boot.actuate.health.HealthIndicator;
import org.springframework.boot.actuate.info.Info;
import org.springframework.boot.actuate.info.InfoContributor;
import org.springframework.stereotype.Component;

import java.util.LinkedHashMap;
import java.util.Map;

@Component("embeddingModel")
public class EmbeddingModelHealthIndicator implements HealthIndicator, InfoContributor {

    private final EmbeddingModel embeddingModel;
    private final String configuredProvider;

    public EmbeddingModelHealthIndicator(
            EmbeddingModel embeddingModel,
            @Value("${spring.ai.model.embedding}") String configuredProvider) {
        this.embeddingModel = embeddingModel;
        this.configuredProvider = configuredProvider;
    }

    @Override
    public Health health() {
        Map<String, Object> details = details();
        Health.Builder builder = Health.up()
                .withDetails(details);
        return builder.build();
    }

    @Override
    public void contribute(Info.Builder builder) {
        builder.withDetail("embeddingModel", details());
    }

    private Map<String, Object> details() {
        boolean localFallback = embeddingModel instanceof LocalHashEmbeddingModel;
        Map<String, Object> details = new LinkedHashMap<>();
        details.put("provider", configuredProvider);
        details.put("implementation", embeddingModel.getClass().getSimpleName());
        details.put("dimensions", embeddingModel.dimensions());
        details.put("productionReady", !localFallback);
        if (localFallback) {
            details.put(
                    "warning",
                    "Lexical hash embeddings are active; configure EMBEDDING_API_KEY "
                            + "for production semantic retrieval");
        }
        return details;
    }
}
