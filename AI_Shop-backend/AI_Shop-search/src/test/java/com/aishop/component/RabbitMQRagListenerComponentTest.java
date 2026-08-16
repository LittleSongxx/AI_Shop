package com.aishop.component;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.http.util.EntityUtils;
import org.elasticsearch.client.Request;
import org.elasticsearch.client.RestClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class RabbitMQRagListenerComponentTest {

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final RestClient restClient = mock(RestClient.class);
    private RabbitMQRagListenerComponent listener;

    @BeforeEach
    void setUp() {
        listener = new RabbitMQRagListenerComponent();
        ReflectionTestUtils.setField(listener, "indexName", "aishop_vectorstore");
        ReflectionTestUtils.setField(listener, "objectMapper", objectMapper);
        ReflectionTestUtils.setField(listener, "restClient", restClient);
    }

    @Test
    void productDeleteQuerySerializesIdentifiersAsJsonValues() throws Exception {
        String productId = "product-\"quoted\\value";

        ReflectionTestUtils.invokeMethod(listener, "deleteByProductId", productId);

        JsonNode query = capturedBody();
        assertEquals(
                productId,
                query.path("query")
                        .path("bool")
                        .path("must")
                        .path(0)
                        .path("term")
                        .path("metadata.productId")
                        .asText());
    }

    @Test
    void staleDeleteQueryPreservesDocumentIdsWithoutStringConcatenation() throws Exception {
        List<String> keepIds = List.of("doc-1", "doc-\"2", "doc-\\3");

        ReflectionTestUtils.invokeMethod(
                listener, "deleteStaleDocuments", "product-1", keepIds);

        JsonNode values = capturedBody()
                .path("query")
                .path("bool")
                .path("must_not")
                .path(0)
                .path("ids")
                .path("values");
        assertEquals(keepIds, objectMapper.convertValue(values, List.class));
    }

    private JsonNode capturedBody() throws Exception {
        ArgumentCaptor<Request> request = ArgumentCaptor.forClass(Request.class);
        verify(restClient).performRequest(request.capture());
        return objectMapper.readTree(EntityUtils.toString(request.getValue().getEntity()));
    }
}
