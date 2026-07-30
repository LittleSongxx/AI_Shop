package com.aishop.component;

import lombok.extern.slf4j.Slf4j;
import org.springframework.ai.chat.messages.SystemMessage;
import org.springframework.ai.chat.messages.UserMessage;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.ai.chat.prompt.Prompt;
import org.springframework.ai.document.Document;
import org.springframework.ai.openai.OpenAiChatOptions;
import org.springframework.ai.vectorstore.VectorStore;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;

import jakarta.annotation.Resource;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * P1-1 Contextual Retrieval: asynchronously enriches published knowledge chunks
 * with a short LLM-generated context prefix.
 *
 * <p>After a document is published to the vector store, each chunk is re-indexed
 * as {@code "contextPrefix\n\noriginalContent"}.  This improves semantic recall
 * for fragments that are opaque when read in isolation (e.g. "符合以上条件的商品可
 * 在7天内退换").
 *
 * <p>All failures are silently swallowed: the chunk remains searchable with its
 * original content.  The original content is stored in
 * {@code metadata.originalContent} for debugging and re-enrichment.
 */
@Component
@Slf4j
public class ContextPrefixEnricher {

    private static final String SYSTEM_PROMPT =
            "你在帮助构建一个电商客服 RAG 系统。给定一个知识文档的标题和其中的一个切片，"
            + "请用 2-3 句话描述该切片在整篇文档中的作用，帮助语义检索理解该切片的上下文。"
            + "只输出描述，不要重复原文，不要加多余标点。";

    /** Max chars sent to LLM — enough for context, cheap for the model. */
    private static final int MAX_EXCERPT_CHARS = 600;

    /** Max original-content chars stored in ES metadata for audit/debug purposes. */
    private static final int MAX_ORIGINAL_STORE_CHARS = 2000;

    @Resource
    private ChatModel chatModel;

    @Resource
    private VectorStore vectorStore;

    /**
     * Asynchronously generate a context prefix and re-index {@code chunkId} with
     * the enriched content.  Called by {@link com.aishop.biz.impl.KnowledgeBaseServiceImpl}
     * after all chunks of a document have been added to the vector store.
     */
    @Async("ragEnrichExecutor")
    public void enrichAsync(
            String chunkId,
            String title,
            String content,
            Map<String, Object> metadata) {
        try {
            String prefix = generatePrefix(title, content);
            if (prefix == null || prefix.isBlank()) {
                return;
            }
            String enriched = prefix + "\n\n" + content;
            Map<String, Object> enrichedMeta = new LinkedHashMap<>(metadata);
            enrichedMeta.put("contextPrefix", prefix);
            enrichedMeta.put("originalContent", content.length() > MAX_ORIGINAL_STORE_CHARS
                    ? content.substring(0, MAX_ORIGINAL_STORE_CHARS) : content);
            // VectorStore.add() upserts by document ID — overwrites the original entry.
            vectorStore.add(List.of(new Document(chunkId, enriched, enrichedMeta)));
            log.debug("rag_context_prefix_written chunkId={} prefixLen={}", chunkId, prefix.length());
        } catch (Exception e) {
            log.warn("rag_context_prefix_failed chunkId={} — original content retained", chunkId, e);
        }
    }

    private String generatePrefix(String title, String content) {
        String excerpt = content.length() > MAX_EXCERPT_CHARS
                ? content.substring(0, MAX_EXCERPT_CHARS) + "…" : content;
        var response = chatModel.call(new Prompt(
                List.of(
                        new SystemMessage(SYSTEM_PROMPT),
                        new UserMessage("文档标题：" + title + "\n切片内容：" + excerpt)
                ),
                OpenAiChatOptions.builder()
                        .temperature(0.0)
                        .maxTokens(120)
                        .build()
        ));
        if (response == null || response.getResult() == null
                || response.getResult().getOutput() == null) {
            return null;
        }
        String text = response.getResult().getOutput().getText();
        return text == null ? null : text.strip();
    }
}
