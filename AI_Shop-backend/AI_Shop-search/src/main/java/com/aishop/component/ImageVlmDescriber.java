package com.aishop.component;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
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

    @Value("${aishop.vlm.connect-timeout-seconds:5}")
    private int connectTimeoutSeconds;

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
        // 一个文档共享一份 client 配置，避免为每张图重复构建客户端。
        // SimpleClientHttpRequestFactory 不承诺连接池复用，因此这里不依赖该语义。
        RestClient client = buildClient();

        List<String> descriptions = new ArrayList<>();
        int limit = Math.min(images.size(), MAX_IMAGES_PER_DOC);
        for (int i = 0; i < limit; i++) {
            byte[] bytes = images.get(i);
            if (bytes == null || bytes.length < MIN_IMAGE_BYTES) {
                continue;
            }
            try {
                String desc = callVlm(bytes, client);
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

    private RestClient buildClient() {
        SimpleClientHttpRequestFactory factory = createRequestFactory(
                connectTimeoutSeconds, timeoutSeconds);
        return RestClient.builder()
                .baseUrl(baseUrl.stripTrailing().replaceAll("/+$", ""))
                .defaultHeader("Authorization", "Bearer " + apiKey)
                .requestFactory(factory)
                .build();
    }

    /**
     * Build the JDK request factory with independent, bounded timeouts.
     * Connection setup is kept short; the read timeout honours the configured
     * VLM latency budget (the old implementation incorrectly capped both at 5s).
     */
    static SimpleClientHttpRequestFactory createRequestFactory(
            int connectTimeoutSeconds, int readTimeoutSeconds) {
        int safeConnectSeconds = Math.min(Math.max(connectTimeoutSeconds, 1), 5);
        int safeReadSeconds = Math.min(Math.max(readTimeoutSeconds, 1), 60);
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout((int) Duration.ofSeconds(safeConnectSeconds).toMillis());
        factory.setReadTimeout((int) Duration.ofSeconds(safeReadSeconds).toMillis());
        return factory;
    }

    private String callVlm(byte[] imageBytes, RestClient client) throws Exception {
        // 按魔数探测真实图片格式，不再写死 image/png：
        // PNG 之外常见的 jpg/gif/webp/bmp 在旧实现下会被 VLM 拒收或描述失真。
        String mime = detectImageMime(imageBytes);
        String dataUri = "data:" + mime + ";base64,"
                + Base64.getEncoder().encodeToString(imageBytes);

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

    /** 按文件头魔数探测常见图片 MIME；认不出的回落到 octet-stream。 */
    static String detectImageMime(byte[] bytes) {
        if (bytes == null || bytes.length < 3) {
            return "application/octet-stream";
        }
        int b0 = bytes[0] & 0xFF;
        int b1 = bytes[1] & 0xFF;
        if (bytes.length >= 8
                && b0 == 0x89 && b1 == 0x50
                && bytes[2] == 0x4E && bytes[3] == 0x47
                && bytes[4] == 0x0D && bytes[5] == 0x0A
                && bytes[6] == 0x1A && bytes[7] == 0x0A) {
            return "image/png";
        }
        if (b0 == 0xFF && b1 == 0xD8 && (bytes[2] & 0xFF) == 0xFF) {
            return "image/jpeg";
        }
        if (bytes.length >= 6
                && b0 == 'G' && b1 == 'I' && bytes[2] == 'F'
                && ((bytes[3] == '8' && bytes[4] == '7' && bytes[5] == 'a')
                    || (bytes[3] == '8' && bytes[4] == '9' && bytes[5] == 'a'))) {
            return "image/gif";
        }
        if (b0 == 'R' && b1 == 'I' && bytes[2] == 'F' && bytes[3] == 'F'
                && bytes.length >= 12
                && bytes[8] == 'W' && bytes[9] == 'E' && bytes[10] == 'B' && bytes[11] == 'P') {
            // RIFF 家族还有 AVI 等其他容器：只查 RIFF+'F' 会把它们误判成
            // image/webp，VLM 会拒收或解析失败（P1 审查：WEBP 魔数不完整）。
            return "image/webp";
        }
        if (b0 == 0x42 && b1 == 0x4D) {
            return "image/bmp";
        }
        return "application/octet-stream";
    }
}
