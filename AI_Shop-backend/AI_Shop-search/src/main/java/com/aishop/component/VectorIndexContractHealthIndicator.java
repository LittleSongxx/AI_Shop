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

    private final RestClient restClient;
    private final ObjectMapper objectMapper;

    @Value("${spring.ai.vectorstore.elasticsearch.index-name}")
    private String indexName;

    @Value("${spring.ai.vectorstore.elasticsearch.embedding-field-name:embedding}")
    private String vectorField;

    @Value("${spring.ai.vectorstore.elasticsearch.dimensions:1024}")
    private int dimensions;

    private volatile Health lastHealth = Health.unknown()
            .withDetail("reason", "vector mapping has not been checked")
            .build();

    public VectorIndexContractHealthIndicator(RestClient restClient, ObjectMapper objectMapper) {
        this.restClient = restClient;
        this.objectMapper = objectMapper;
    }

    @Override
    public void run(ApplicationArguments args) {
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
            lastHealth = error
                    .map(message -> Health.down()
                            .withDetail("index", indexName)
                            .withDetail("field", vectorField)
                            .withDetail("expectedDimensions", dimensions)
                            .withDetail("error", message)
                            .build())
                    .orElseGet(() -> Health.up()
                            .withDetail("index", indexName)
                            .withDetail("field", vectorField)
                            .withDetail("dimensions", dimensions)
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

    static Optional<String> validateMapping(
            JsonNode root, String indexName, String vectorField, int dimensions) {
        JsonNode indexNode = root.path(indexName);
        if (indexNode.isMissingNode() && root.isObject() && root.size() == 1) {
            Iterator<Map.Entry<String, JsonNode>> fields = root.fields();
            indexNode = fields.hasNext() ? fields.next().getValue() : indexNode;
        }
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
        return Optional.empty();
    }
}
