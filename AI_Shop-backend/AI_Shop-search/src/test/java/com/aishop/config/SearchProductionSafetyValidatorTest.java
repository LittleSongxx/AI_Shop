package com.aishop.config;

import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

class SearchProductionSafetyValidatorTest {

    @Test
    void productionRejectsPlainHttpElasticsearch() {
        SearchProductionSafetyValidator validator = validator(
                "http://elasticsearch.internal:9200",
                "aishop-search",
                "secret");

        assertThrows(IllegalStateException.class, validator::validate);
    }

    @Test
    void productionRejectsMissingElasticsearchCredentials() {
        SearchProductionSafetyValidator validator = validator(
                "https://elasticsearch.internal:9200",
                "",
                "");

        assertThrows(IllegalStateException.class, validator::validate);
    }

    @Test
    void productionAcceptsAuthenticatedHttpsElasticsearch() {
        assertDoesNotThrow(() -> validator(
                "https://elasticsearch.internal:9200",
                "aishop-search",
                "secret",
                "openai",
                "embedding-secret",
                false).validate());
    }

    @Test
    void productionRejectsLocalEmbeddingFallback() {
        SearchProductionSafetyValidator validator = validator(
                "https://elasticsearch.internal:9200",
                "aishop-search",
                "secret",
                "local",
                "",
                false);

        assertThrows(IllegalStateException.class, validator::validate);
    }

    @Test
    void productionRejectsAutomaticVectorSchemaInitialization() {
        SearchProductionSafetyValidator validator = validator(
                "https://elasticsearch.internal:9200",
                "aishop-search",
                "secret",
                "openai",
                "embedding-secret",
                true);

        assertThrows(IllegalStateException.class, validator::validate);
    }

    private static SearchProductionSafetyValidator validator(
            String uris,
            String username,
            String password) {
        return validator(
                uris,
                username,
                password,
                "openai",
                "embedding-secret",
                false);
    }

    private static SearchProductionSafetyValidator validator(
            String uris,
            String username,
            String password,
            String embeddingProvider,
            String embeddingApiKey,
            boolean initializeVectorSchema) {
        SearchProductionSafetyValidator validator =
                new SearchProductionSafetyValidator();
        ReflectionTestUtils.setField(validator, "productionReady", true);
        ReflectionTestUtils.setField(validator, "elasticsearchUris", uris);
        ReflectionTestUtils.setField(validator, "elasticsearchUsername", username);
        ReflectionTestUtils.setField(validator, "elasticsearchPassword", password);
        ReflectionTestUtils.setField(validator, "embeddingProvider", embeddingProvider);
        ReflectionTestUtils.setField(validator, "embeddingApiKey", embeddingApiKey);
        ReflectionTestUtils.setField(
                validator, "initializeVectorSchema", initializeVectorSchema);
        return validator;
    }
}
