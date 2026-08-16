package com.aishop.component;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.http.util.EntityUtils;
import org.elasticsearch.client.Request;
import org.elasticsearch.client.Response;
import org.elasticsearch.client.RestClient;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.boot.actuate.health.Health;
import org.springframework.boot.actuate.health.HealthIndicator;
import org.springframework.stereotype.Component;

import java.util.Iterator;
import java.util.Map;
import java.util.Optional;

@Component("vectorIndexContract")
public class VectorIndexContractHealthIndicator implements HealthIndicator, ApplicationRunner {

    private static final String VECTOR_SIMILARITY = "cosine";
    private static final String VECTOR_INDEX_TYPE = "int8_hnsw";
    private static final int VECTOR_HNSW_M = 16;
    private static final int VECTOR_HNSW_EF_CONSTRUCTION = 100;

    private final RestClient restClient;
    private final ObjectMapper objectMapper;

    @Value("${spring.ai.vectorstore.elasticsearch.index-name}")
    private String indexName;

    @Value("${spring.ai.vectorstore.elasticsearch.embedding-field-name:embedding}")
    private String vectorField;

    @Value("${spring.ai.vectorstore.elasticsearch.dimensions:1024}")
    private int dimensions;

    @Value("${spring.ai.model.embedding}")
    private String embeddingProvider;

    @Value("${spring.ai.openai.embedding.options.model:text-embedding-v4}")
    private String configuredEmbeddingModel;

    @Value("${aishop.search.vector-contract.version:1}")
    private int contractVersion;

    private volatile Health lastHealth = Health.unknown()
            .withDetail("reason", "vector mapping has not been checked")
            .build();

    public VectorIndexContractHealthIndicator(RestClient restClient, ObjectMapper objectMapper) {
        this.restClient = restClient;
        this.objectMapper = objectMapper;
    }

    @Override
    public void run(ApplicationArguments args) {
        initializeContractMetadataWhenEmpty();
        refresh();
    }

    @Override
    public Health health() {
        return refresh();
    }

    private synchronized Health refresh() {
        try {
            Response response = restClient.performRequest(
                    new Request("GET", "/" + indexName + "/_mapping"));
            JsonNode mapping = objectMapper.readTree(EntityUtils.toString(response.getEntity()));
            Optional<String> error = validateMapping(mapping, indexName, vectorField, dimensions);
            if (error.isEmpty()) {
                error = validateEmbeddingContract(
                        mapping,
                        indexName,
                        embeddingProvider,
                        effectiveEmbeddingModel(),
                        dimensions,
                        contractVersion);
            }
            lastHealth = error
                    .map(message -> Health.down()
                            .withDetail("index", indexName)
                            .withDetail("field", vectorField)
                            .withDetail("expectedDimensions", dimensions)
                            .withDetail("embeddingProvider", embeddingProvider)
                            .withDetail("embeddingModel", effectiveEmbeddingModel())
                            .withDetail("contractVersion", contractVersion)
                            .withDetail("error", message)
                            .build())
                    .orElseGet(() -> Health.up()
                            .withDetail("index", indexName)
                            .withDetail("field", vectorField)
                            .withDetail("dimensions", dimensions)
                            .withDetail("embeddingProvider", embeddingProvider)
                            .withDetail("embeddingModel", effectiveEmbeddingModel())
                            .withDetail("contractVersion", contractVersion)
                            .build());
        } catch (Exception exception) {
            lastHealth = Health.down()
                    .withDetail("index", indexName)
                    .withDetail("field", vectorField)
                    .withDetail("expectedDimensions", dimensions)
                    .withDetail("error", exception.getClass().getSimpleName())
                    .build();
        }
        return lastHealth;
    }

    private void initializeContractMetadataWhenEmpty() {
        try {
            Response mappingResponse = restClient.performRequest(
                    new Request("GET", "/" + indexName + "/_mapping"));
            JsonNode mapping = objectMapper.readTree(
                    EntityUtils.toString(mappingResponse.getEntity()));
            if (validateMapping(mapping, indexName, vectorField, dimensions).isPresent()
                    || hasEmbeddingContract(mapping, indexName)) {
                return;
            }
            Response countResponse = restClient.performRequest(
                    new Request("GET", "/" + indexName + "/_count"));
            long count = objectMapper.readTree(
                    EntityUtils.toString(countResponse.getEntity()))
                    .path("count")
                    .asLong(-1L);
            if (count != 0L) {
                return;
            }

            Map<String, Object> contract = Map.of(
                    "embeddingProvider", embeddingProvider,
                    "embeddingModel", effectiveEmbeddingModel(),
                    "embeddingDimensions", dimensions,
                    "contractVersion", contractVersion);
            Request update = new Request("PUT", "/" + indexName + "/_mapping");
            update.setJsonEntity(objectMapper.writeValueAsString(
                    Map.of("_meta", Map.of("aishopEmbeddingContract", contract))));
            restClient.performRequest(update);
        } catch (Exception ignored) {
            // health() exposes the actionable mapping error after startup.
        }
    }

    private String effectiveEmbeddingModel() {
        return "local".equalsIgnoreCase(embeddingProvider)
                ? "local-hash-v1"
                : configuredEmbeddingModel;
    }

    static Optional<String> validateMapping(
            JsonNode root, String indexName, String vectorField, int dimensions) {
        JsonNode indexNode = indexNode(root, indexName);
        JsonNode field = indexNode.path("mappings").path("properties").path(vectorField);
        String type = field.path("type").asText("");
        if (!"dense_vector".equals(type)) {
            return Optional.of(
                    "expected dense_vector field '" + vectorField + "', got "
                            + (type.isBlank() ? "missing" : type));
        }
        int actualDimensions = field.path("dims").asInt(-1);
        if (actualDimensions != dimensions) {
            return Optional.of(
                    "expected " + dimensions + " dimensions, got "
                            + (actualDimensions < 0 ? "missing" : actualDimensions));
        }
        if (!field.path("index").asBoolean(false)) {
            return Optional.of("expected indexed dense_vector field '" + vectorField + "'");
        }
        String similarity = field.path("similarity").asText("");
        if (!VECTOR_SIMILARITY.equals(similarity)) {
            return Optional.of(
                    "expected " + VECTOR_SIMILARITY + " similarity, got "
                            + (similarity.isBlank() ? "missing" : similarity));
        }
        JsonNode indexOptions = field.path("index_options");
        String indexType = indexOptions.path("type").asText("");
        int hnswM = indexOptions.path("m").asInt(-1);
        int efConstruction = indexOptions.path("ef_construction").asInt(-1);
        if (!VECTOR_INDEX_TYPE.equals(indexType)
                || hnswM != VECTOR_HNSW_M
                || efConstruction != VECTOR_HNSW_EF_CONSTRUCTION) {
            return Optional.of(
                    "expected " + VECTOR_INDEX_TYPE
                            + " index options m=" + VECTOR_HNSW_M
                            + ", ef_construction=" + VECTOR_HNSW_EF_CONSTRUCTION);
        }
        return Optional.empty();
    }

    static Optional<String> validateEmbeddingContract(
            JsonNode root,
            String indexName,
            String expectedProvider,
            String expectedModel,
            int expectedDimensions,
            int expectedVersion) {
        JsonNode contract = indexNode(root, indexName)
                .path("mappings")
                .path("_meta")
                .path("aishopEmbeddingContract");
        if (contract.isMissingNode() || !contract.isObject()) {
            return Optional.of("embedding contract metadata is missing");
        }
        String actualProvider = contract.path("embeddingProvider").asText("");
        String actualModel = contract.path("embeddingModel").asText("");
        int actualDimensions = contract.path("embeddingDimensions").asInt(-1);
        int actualVersion = contract.path("contractVersion").asInt(-1);
        if (!expectedProvider.equalsIgnoreCase(actualProvider)
                || !expectedModel.equals(actualModel)
                || expectedDimensions != actualDimensions
                || expectedVersion != actualVersion) {
            return Optional.of(
                    "expected embedding contract "
                            + expectedProvider + "/" + expectedModel
                            + "/" + expectedDimensions + "/v" + expectedVersion
                            + ", got "
                            + actualProvider + "/" + actualModel
                            + "/" + actualDimensions + "/v" + actualVersion);
        }
        return Optional.empty();
    }

    private static boolean hasEmbeddingContract(JsonNode root, String indexName) {
        return indexNode(root, indexName)
                .path("mappings")
                .path("_meta")
                .path("aishopEmbeddingContract")
                .isObject();
    }

    private static JsonNode indexNode(JsonNode root, String indexName) {
        JsonNode result = root.path(indexName);
        if (result.isMissingNode() && root.isObject() && root.size() == 1) {
            Iterator<Map.Entry<String, JsonNode>> fields = root.fields();
            result = fields.hasNext() ? fields.next().getValue() : result;
        }
        return result;
    }
}
