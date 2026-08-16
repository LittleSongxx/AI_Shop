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
                        "embedding": {
                          "type": "dense_vector",
                          "dims": 1024,
                          "index": true,
                          "similarity": "cosine",
                          "index_options": {
                            "type": "int8_hnsw",
                            "m": 16,
                            "ef_construction": 100
                          }
                        }
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
    void rejectsAChangedVectorSimilarityOrIndexAlgorithm() throws Exception {
        String mapping = """
                {
                  "aishop_vectorstore": {
                    "mappings": {
                      "properties": {
                        "embedding": {
                          "type": "dense_vector",
                          "dims": 1024,
                          "index": true,
                          "similarity": "dot_product",
                          "index_options": {
                            "type": "hnsw",
                            "m": 16,
                            "ef_construction": 100
                          }
                        }
                      }
                    }
                  }
                }
                """;

        assertTrue(VectorIndexContractHealthIndicator.validateMapping(
                objectMapper.readTree(mapping),
                "aishop_vectorstore",
                "embedding",
                1024).isPresent());
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

    @Test
    void validatesEmbeddingProviderModelAndSchemaVersion() throws Exception {
        String mapping = """
                {
                  "aishop_vectorstore": {
                    "mappings": {
                      "_meta": {
                        "aishopEmbeddingContract": {
                          "embeddingProvider": "local",
                          "embeddingModel": "local-hash-v1",
                          "embeddingDimensions": 1024,
                          "contractVersion": 1
                        }
                      },
                      "properties": {
                        "embedding": {"type": "dense_vector", "dims": 1024}
                      }
                    }
                  }
                }
                """;

        assertTrue(VectorIndexContractHealthIndicator.validateEmbeddingContract(
                objectMapper.readTree(mapping),
                "aishop_vectorstore",
                "local",
                "local-hash-v1",
                1024,
                1).isEmpty());
        assertTrue(VectorIndexContractHealthIndicator.validateEmbeddingContract(
                objectMapper.readTree(mapping),
                "aishop_vectorstore",
                "openai",
                "text-embedding-v4",
                1024,
                1).isPresent());
    }
}
