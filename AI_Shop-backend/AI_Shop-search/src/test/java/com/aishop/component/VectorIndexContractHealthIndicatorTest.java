package com.aishop.component;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertTrue;

class VectorIndexContractHealthIndicatorTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void acceptsTheSharedEmbeddingContract() throws Exception {
        String mapping = """
                {
                  "aishop_vectorstore": {
                    "mappings": {
                      "properties": {
                        "embedding": {"type": "dense_vector", "dims": 1024}
                      }
                    }
                  }
                }
                """;

        assertTrue(VectorIndexContractHealthIndicator.validateMapping(
                objectMapper.readTree(mapping),
                "aishop_vectorstore",
                "embedding",
                1024).isEmpty());
    }

    @Test
    void rejectsNonContractFieldsAndWrongDimensions() throws Exception {
        String wrongMapping = """
                {
                  "aishop_vectorstore": {
                    "mappings": {
                      "properties": {
                        "wrong_vector": {"type": "dense_vector", "dims": 768}
                      }
                    }
                  }
                }
                """;

        assertTrue(VectorIndexContractHealthIndicator.validateMapping(
                objectMapper.readTree(wrongMapping),
                "aishop_vectorstore",
                "embedding",
                1024).isPresent());
    }
}
