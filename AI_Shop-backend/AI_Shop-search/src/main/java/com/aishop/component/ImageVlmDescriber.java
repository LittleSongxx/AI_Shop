package com.aishop.component;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Map;

/**
 * P3-2 Multimodal RAG: calls a VLM (e.g. Qwen-VL via DashScope) to produce a
 * plain-text description for images embedded in knowledge documents.
 *
 * <p>Disabled by default ({@code aishop.vlm.enabled=false}) so the document-parse
 * path is unaffected until VLM credentials are configured.  All failures return
 * silently; callers receive an empty list and must handle gracefully.
 *
 * <p>Images are sent as base64 data URIs, avoiding object-storage dependencies.
 */
@Component
@Slf4j
public class ImageVlmDescriber {

    private static final String SYSTEM_PROMPT =
            "你是电商客服 RAG 系统的图片分析助手。"
            + "请用 1-3 句简洁中文描述图片的主要内容，"
            + "重点说明商品外观、型号、包装及文字标识等与购物相关的可见信息。"
            + "只输出描述本身，不含解释或前缀。";

    /** Byte-size threshold below which images are likely icons or artefacts. */
    private static final int MIN_IMAGE_BYTES = 1024;

    /** Cap to limit VLM latency on image-heavy PDFs. */
    private static final int MAX_IMAGES_PER_DOC = 10;

    @Value("${aishop.vlm.enabled:false}")
    private boolean enabled;

    @Value("${aishop.vlm.api-key:}")
    private String apiKey;

    @Value("${aishop.vlm.base-url:https://dashscope.aliyuncs.com/compatible-mode/v1}")
    private String baseUrl;

    @Value("${aishop.vlm.model:qwen-vl-plus}")
    private String model;

    @Value("${aishop.vlm.max-tokens:150}")
    private int maxTokens;

    @Value("${aishop.vlm.timeout-seconds:15}")
    private int timeoutSeconds;

    private final ObjectMapper objectMapper;

    public ImageVlmDescriber(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    /** Returns true when VLM is both enabled and has a non-blank API key. */
    public boolean isEnabled() {
        return enabled && apiKey != null && !apiKey.isBlank();
    }

    /**
     * Describe up to {@value #MAX_IMAGES_PER_DOC} images and return their descriptions.
     * Images that are too small, or whose VLM calls fail, are silently skipped.
     *
     * @param images raw image bytes extracted from a document (e.g. via Tika)
     * @return non-empty description strings, may be an empty list
     */
    public List<String> describeAll(List<byte[]> images) {
        if (!isEnabled() || images == null || images.isEmpty()) {
            return List.of();
        }
        List<String> descriptions = new ArrayList<>();
        int limit = Math.min(images.size(), MAX_IMAGES_PER_DOC);
        for (int i = 0; i < limit; i++) {
            byte[] bytes = images.get(i);
            if (bytes == null || bytes.length < MIN_IMAGE_BYTES) {
                continue;
            }
            try {
                String desc = callVlm(bytes);
                if (desc != null && !desc.isBlank()) {
                    descriptions.add(desc);
                    log.debug("vlm_image_described index={} length={}", i, desc.length());
                }
            } catch (Exception e) {
                log.warn("vlm_image_describe_failed index={} — skipped", i, e);
            }
        }
        return descriptions;
    }

    private String callVlm(byte[] imageBytes) throws Exception {
        String dataUri = "data:image/png;base64,"
                + Base64.getEncoder().encodeToString(imageBytes);

        RestClient client = RestClient.builder()
                .baseUrl(baseUrl.stripTrailing().replaceAll("/+$", ""))
                .defaultHeader("Authorization", "Bearer " + apiKey)
                .build();

        Map<String, Object> body = Map.of(
                "model", model,
                "max_tokens", maxTokens,
                "temperature", 0,
                "messages", List.of(
                        Map.of("role", "system", "content", SYSTEM_PROMPT),
                        Map.of("role", "user", "content", List.of(
                                Map.of("type", "image_url",
                                        "image_url", Map.of("url", dataUri))
                        ))
                )
        );

        String responseBody = client.post()
                .uri("/chat/completions")
                .contentType(MediaType.APPLICATION_JSON)
                .body(body)
                .retrieve()
                .onStatus(status -> status.isError(),
                        (req, resp) -> {
                            throw new RuntimeException(
                                    "VLM API error " + resp.getStatusCode());
                        })
                .body(String.class);

        if (responseBody == null) {
            return null;
        }
        JsonNode root = objectMapper.readTree(responseBody);
        JsonNode choices = root.path("choices");
        if (choices.isEmpty()) {
            return null;
        }
        String text = choices.get(0).path("message").path("content").asText("").strip();
        return text.isBlank() ? null : text;
    }
}
