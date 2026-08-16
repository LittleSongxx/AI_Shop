package com.aishop.component;

import org.springframework.ai.embedding.EmbeddingModel;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration(proxyBeanMethods = false)
@ConditionalOnProperty(
        name = "spring.ai.model.embedding",
        havingValue = "local")
public class LocalEmbeddingConfiguration {

    @Bean
    @ConditionalOnMissingBean(EmbeddingModel.class)
    public EmbeddingModel localEmbeddingModel(
            @Value("${aishop.search.local-embedding.dimensions:1024}") int dimensions) {
        return new LocalHashEmbeddingModel(dimensions);
    }
}
