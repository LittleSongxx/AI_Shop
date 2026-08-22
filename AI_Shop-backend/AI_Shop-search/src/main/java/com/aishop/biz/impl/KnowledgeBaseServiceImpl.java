package com.aishop.biz.impl;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.aishop.biz.KnowledgeBaseService;
import com.aishop.component.ContextPrefixEnricher;
import com.aishop.component.InjectionScanner;
import com.aishop.component.KnowledgeDocumentParser;
import com.aishop.component.KnowledgeDocumentParser.Chunk;
import com.aishop.component.KnowledgeDocumentParser.ParsedDocument;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.TransactionalMqSender;
import com.aishop.entity.dto.RagDataDTO;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.enums.RagDataTypeEnum;
import com.aishop.exception.BusinessException;
import com.aishop.support.MqIdempotencyKeys;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.ai.document.Document;
import org.springframework.ai.vectorstore.VectorStore;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.multipart.MultipartFile;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.PreparedStatement;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

@Service
@Slf4j
public class KnowledgeBaseServiceImpl implements KnowledgeBaseService {

    private static final String RELEASE_KEY = "mall:knowledge:version";
    private static final String RELEASE_TOPIC = "knowledge.release";
    private static final int INDEX_SCHEMA_VERSION = 1;
    private static final DateTimeFormatter TIME_FORMATTER =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    @Resource
    private JdbcTemplate jdbcTemplate;
    @Resource
    private KnowledgeDocumentParser documentParser;
    @Resource
    private VectorStore vectorStore;
    @Resource
    private ObjectMapper objectMapper;
    @Resource
    private StringRedisTemplate stringRedisTemplate;
    @Resource
    private TransactionalMqSender transactionalMqSender;
    @Autowired(required = false)
    private ContextPrefixEnricher contextPrefixEnricher;
    @Resource
    private InjectionScanner injectionScanner;

    @Override
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> upload(
            MultipartFile file, String title, String owner, String domain) {
        ParsedDocument parsed = documentParser.parse(file);
        String resolvedTitle = text(title).isBlank()
                ? stripExtension(parsed.sourceName()) : text(title);
        String resolvedDomain = normalizeDomain(domain);
        String hash = sha256(parsed.normalizedText());
        List<Map<String, Object>> existing = jdbcTemplate.queryForList(
                "SELECT document_id FROM knowledge_document WHERE content_hash=? LIMIT 1", hash);
        if (!existing.isEmpty()) {
            return getDocument(((Number) existing.get(0).get("document_id")).longValue());
        }

        long documentId = insertDocument(
                resolvedTitle, parsed, hash, owner, resolvedDomain);
        long jobId = insertJob(documentId, "RUNNING", "CHUNK", 30);
        try {
            // 入库时预扫描：检测切片内容中是否含有注入话术。
            // 发现污染时抛出 ContaminatedDocumentException，由上层返回 400；
            // 不进向量库，同时 warn 日志提供可定位信号。
            for (KnowledgeDocumentParser.Chunk chunk : parsed.chunks()) {
                injectionScanner.scan(chunk.content());
            }
            insertChunks(
                    documentId,
                    1,
                    parsed.chunks(),
                    resolvedTitle,
                    parsed.sourceName(),
                    resolvedDomain);
            jdbcTemplate.update(
                    "UPDATE knowledge_document SET status='READY', updated_at=NOW() WHERE document_id=?",
                    documentId);
            jdbcTemplate.update(
                    """
                    UPDATE knowledge_ingest_job
                    SET status='READY', stage='WAIT_PUBLISH', progress=80, chunk_count=?,
                        updated_at=NOW()
                    WHERE job_id=?
                    """,
                    parsed.chunks().size(), jobId);
            return getDocument(documentId);
        } catch (RuntimeException e) {
            markJobFailed(jobId, documentId, e);
            throw e;
        }
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> publish(long documentId, String owner) {
        Map<String, Object> document = requireDocumentForUpdate(documentId);
        if ("ARCHIVED".equals(document.get("status"))) {
            throw new BusinessException("归档文档不能发布");
        }
        if ("PUBLISHED".equals(document.get("status"))) {
            // 当前模型没有“编辑后生成新文档版本”的入口。允许重复发布只会
            // 覆盖同一批 ES ID；第二次失败清理时还会删掉第一次的有效副本。
            throw new BusinessException("知识文档已经发布，请勿重复发布");
        }
        int version = number(document.get("version"), 1);
        List<Map<String, Object>> chunks = jdbcTemplate.queryForList(
                """
                SELECT chunk_id, chunk_index, heading, content, token_count
                FROM knowledge_chunk
                WHERE document_id=? AND version=?
                ORDER BY chunk_index
                """,
                documentId, version);
        if (chunks.isEmpty()) {
            throw new BusinessException("文档没有可发布的切片");
        }

        // 锁住数据库全局版本行直到事务提交。这个锁跨 JVM 生效，保证两个管理端
        // 实例不会算出相同 nextRelease；Java synchronized 只能保护单进程。
        long currentRelease = lockReleaseVersion();
        long nextRelease = currentRelease + 1;
        List<String> writtenIds = new ArrayList<>();
        long jobId = insertJob(documentId, "RUNNING", "INDEX", 85);
        try {
            List<Document> toEnrich = new ArrayList<>();
            List<Document> batch = new ArrayList<>();
            for (Map<String, Object> chunk : chunks) {
                String originalContent = String.valueOf(chunk.get("content"));
                Map<String, Object> metadata = knowledgeMetadata(
                        document, chunk, nextRelease, originalContent);
                Document doc = new Document(
                        String.valueOf(chunk.get("chunk_id")),
                        originalContent,
                        metadata);
                batch.add(doc);
                toEnrich.add(doc);
                writtenIds.add(doc.getId());
                if (batch.size() == 10) {
                    vectorStore.add(batch);
                    batch = new ArrayList<>();
                }
            }
            if (!batch.isEmpty()) {
                vectorStore.add(batch);
            }
            jdbcTemplate.update(
                    """
                    UPDATE knowledge_document
                    SET status='PUBLISHED', index_schema_version=?,
                        owner=COALESCE(NULLIF(?, ''), owner), updated_at=NOW()
                    WHERE document_id=?
                    """,
                    INDEX_SCHEMA_VERSION, text(owner), documentId);
            jdbcTemplate.update(
                    """
                    UPDATE knowledge_chunk SET status='PUBLISHED', updated_at=NOW()
                    WHERE document_id=? AND version=?
                    """,
                    documentId, version);
            jdbcTemplate.update(
                    """
                    UPDATE knowledge_ingest_job
                    SET status='SUCCESS', stage='PUBLISHED', progress=100,
                        chunk_count=?, updated_at=NOW()
                    WHERE job_id=?
                    """,
                    chunks.size(), jobId);
            long releaseVersion = advanceReleaseVersion(currentRelease);
            snapshotCurrentRelease(
                    releaseVersion,
                    "publish-document-" + documentId,
                    owner,
                    null);
            String title = String.valueOf(document.get("title"));
            registerAfterCommit(() -> {
                publishVersionBestEffort(releaseVersion, "知识发布");
                if (contextPrefixEnricher == null) {
                    return;
                }
                // 只有数据库提交后才允许异步上下文增强。若事务回滚，后台任务
                // 不能把已清理的失败切片重新写回 ES。
                for (Document doc : toEnrich) {
                    try {
                        contextPrefixEnricher.enrichAsync(
                                doc.getId(), title, doc.getText(), doc.getMetadata());
                    } catch (RuntimeException e) {
                        log.warn("知识切片上下文增强调度失败, chunkId={}", doc.getId(), e);
                    }
                }
            });
            Map<String, Object> result = getDocument(documentId);
            result.put("releaseVersion", releaseVersion);
            return result;
        } catch (RuntimeException e) {
            // 事务会回滚文档状态和全局版本。活跃文档目录只包含 DB 中
            // PUBLISHED 的 documentId，因此即便 ES 清理部分失败，残留也不可见。
            if (!writtenIds.isEmpty()) {
                try {
                    vectorStore.delete(writtenIds);
                } catch (RuntimeException cleanup) {
                    log.warn("发布失败后清理切片不完整，活跃目录将隔离残留, documentId={}",
                            documentId, cleanup);
                }
            }
            throw new BusinessException("知识文档发布失败：" + e.getMessage(), e);
        }
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> reindexPublished(long documentId, String owner) {
        Map<String, Object> document = requireDocumentForUpdate(documentId);
        if (!"PUBLISHED".equals(document.get("status"))) {
            throw new BusinessException("只有已发布知识文档可以重建索引");
        }
        if (number(document.get("indexSchemaVersion"), 0) >= INDEX_SCHEMA_VERSION) {
            Map<String, Object> result = new LinkedHashMap<>(document);
            result.put("reindexed", false);
            result.put("releaseVersion", releaseVersion());
            return result;
        }

        int version = number(document.get("version"), 1);
        List<Map<String, Object>> chunks = jdbcTemplate.queryForList(
                """
                SELECT chunk_id, chunk_index, heading, content, token_count
                FROM knowledge_chunk
                WHERE document_id=? AND version=? AND status='PUBLISHED'
                ORDER BY chunk_index
                """,
                documentId, version);
        if (chunks.isEmpty()) {
            throw new BusinessException("已发布文档没有可重建的切片");
        }

        long currentRelease = lockReleaseVersion();
        long jobId = insertJob(documentId, "RUNNING", "REINDEX", 85);
        List<Document> toEnrich = new ArrayList<>();
        try {
            List<Document> batch = new ArrayList<>();
            for (Map<String, Object> chunk : chunks) {
                String originalContent = String.valueOf(chunk.get("content"));
                Map<String, Object> metadata = knowledgeMetadata(
                        document, chunk, currentRelease, originalContent);
                Document indexed = new Document(
                        String.valueOf(chunk.get("chunk_id")),
                        originalContent,
                        metadata);
                batch.add(indexed);
                toEnrich.add(indexed);
                if (batch.size() == 10) {
                    vectorStore.add(batch);
                    batch = new ArrayList<>();
                }
            }
            if (!batch.isEmpty()) {
                vectorStore.add(batch);
            }
            jdbcTemplate.update(
                    """
                    UPDATE knowledge_document
                    SET index_schema_version=?, owner=COALESCE(NULLIF(?, ''), owner),
                        updated_at=NOW()
                    WHERE document_id=?
                    """,
                    INDEX_SCHEMA_VERSION, text(owner), documentId);
            jdbcTemplate.update(
                    """
                    UPDATE knowledge_ingest_job
                    SET status='SUCCESS', stage='REINDEXED', progress=100,
                        chunk_count=?, updated_at=NOW()
                    WHERE job_id=?
                    """,
                    chunks.size(), jobId);
            long releaseVersion = advanceReleaseVersion(currentRelease);
            snapshotCurrentRelease(
                    releaseVersion,
                    "reindex-document-" + documentId,
                    owner,
                    null);
            String title = String.valueOf(document.get("title"));
            registerAfterCommit(() -> {
                publishVersionBestEffort(releaseVersion, "知识索引契约升级");
                if (contextPrefixEnricher == null) {
                    return;
                }
                for (Document indexed : toEnrich) {
                    try {
                        contextPrefixEnricher.enrichAsync(
                                indexed.getId(), title, indexed.getText(), indexed.getMetadata());
                    } catch (RuntimeException e) {
                        log.warn("知识重索引后上下文增强调度失败, chunkId={}", indexed.getId(), e);
                    }
                }
            });
            Map<String, Object> result = getDocument(documentId);
            result.put("reindexed", true);
            result.put("releaseVersion", releaseVersion);
            return result;
        } catch (RuntimeException e) {
            markReindexJobFailed(jobId, e);
            throw new BusinessException("知识文档重建索引失败：" + e.getMessage(), e);
        }
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> rebuildAllPublishedVectors() {
        List<Long> documentIds = jdbcTemplate.queryForList(
                """
                SELECT document_id
                FROM knowledge_document
                WHERE status='PUBLISHED'
                ORDER BY document_id
                """,
                Long.class);
        if (documentIds.isEmpty()) {
            return Map.of(
                    "documents", 0,
                    "chunks", 0,
                    "releaseVersion", releaseVersion());
        }

        long currentRelease = lockReleaseVersion();
        List<String> writtenIds = new ArrayList<>();
        List<EnrichmentCandidate> enrichmentCandidates = new ArrayList<>();
        int totalChunks = 0;
        try {
            for (Long documentId : documentIds) {
                Map<String, Object> document = requireDocumentForUpdate(documentId);
                int version = number(document.get("version"), 1);
                List<Map<String, Object>> chunks = jdbcTemplate.queryForList(
                        """
                        SELECT chunk_id, chunk_index, heading, content, token_count
                        FROM knowledge_chunk
                        WHERE document_id=? AND version=? AND status='PUBLISHED'
                        ORDER BY chunk_index
                        """,
                        documentId, version);
                if (chunks.isEmpty()) {
                    throw new BusinessException(
                            "已发布知识文档没有可重建的切片, documentId=" + documentId);
                }

                long jobId = insertJob(documentId, "RUNNING", "MODEL_REINDEX", 85);
                List<Document> batch = new ArrayList<>();
                String title = String.valueOf(document.get("title"));
                for (Map<String, Object> chunk : chunks) {
                    String originalContent = String.valueOf(chunk.get("content"));
                    Document indexed = new Document(
                            String.valueOf(chunk.get("chunk_id")),
                            originalContent,
                            knowledgeMetadata(
                                    document, chunk, currentRelease, originalContent));
                    batch.add(indexed);
                    writtenIds.add(indexed.getId());
                    enrichmentCandidates.add(new EnrichmentCandidate(title, indexed));
                    if (batch.size() == 10) {
                        vectorStore.add(batch);
                        batch = new ArrayList<>();
                    }
                }
                if (!batch.isEmpty()) {
                    vectorStore.add(batch);
                }
                jdbcTemplate.update(
                        """
                        UPDATE knowledge_document
                        SET index_schema_version=?, updated_at=NOW()
                        WHERE document_id=?
                        """,
                        INDEX_SCHEMA_VERSION, documentId);
                jdbcTemplate.update(
                        """
                        UPDATE knowledge_ingest_job
                        SET status='SUCCESS', stage='MODEL_REINDEXED', progress=100,
                            chunk_count=?, updated_at=NOW()
                        WHERE job_id=?
                        """,
                        chunks.size(), jobId);
                totalChunks += chunks.size();
            }

            long releaseVersion = advanceReleaseVersion(currentRelease);
            snapshotCurrentRelease(
                    releaseVersion,
                    "rebuild-published-vectors",
                    "system",
                    null);
            registerAfterCommit(() -> {
                publishVersionBestEffort(releaseVersion, "知识向量模型重建");
                if (contextPrefixEnricher == null) {
                    return;
                }
                for (EnrichmentCandidate candidate : enrichmentCandidates) {
                    Document indexed = candidate.document();
                    try {
                        contextPrefixEnricher.enrichAsync(
                                indexed.getId(),
                                candidate.title(),
                                indexed.getText(),
                                indexed.getMetadata());
                    } catch (RuntimeException e) {
                        log.warn("知识模型重建后上下文增强调度失败, chunkId={}",
                                indexed.getId(), e);
                    }
                }
            });
            return Map.of(
                    "documents", documentIds.size(),
                    "chunks", totalChunks,
                    "releaseVersion", releaseVersion);
        } catch (RuntimeException e) {
            if (!writtenIds.isEmpty()) {
                try {
                    vectorStore.delete(writtenIds);
                } catch (RuntimeException cleanup) {
                    log.warn("知识向量模型重建失败后清理不完整", cleanup);
                }
            }
            throw new BusinessException("已发布知识向量全量重建失败：" + e.getMessage(), e);
        }
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> archive(long documentId) {
        Map<String, Object> document = requireDocumentForUpdate(documentId);
        if ("ARCHIVED".equals(document.get("status"))) {
            Map<String, Object> result = new LinkedHashMap<>(document);
            result.put("releaseVersion", releaseVersion());
            result.put("vectorsRetainedForHistoricalRelease", true);
            return result;
        }

        long currentRelease = lockReleaseVersion();
        long version = advanceReleaseVersion(currentRelease);
        jdbcTemplate.update(
                "UPDATE knowledge_document SET status='ARCHIVED', updated_at=NOW() WHERE document_id=?",
                documentId);
        jdbcTemplate.update(
                "UPDATE knowledge_chunk SET status='ARCHIVED', updated_at=NOW() WHERE document_id=?",
                documentId);
        snapshotCurrentRelease(
                version,
                "archive-document-" + documentId,
                "system",
                null);
        Map<String, Object> result = new LinkedHashMap<>(document);
        result.put("status", "ARCHIVED");
        result.put("releaseVersion", version);
        result.put("vectorsRetainedForHistoricalRelease", true);
        registerAfterCommit(() -> {
            publishVersionBestEffort(version, "知识归档");
        });
        return result;
    }

    @Override
    public Map<String, Object> listDocuments(
            int pageNo, int pageSize, String status) {
        int safePage = Math.max(1, pageNo);
        int safeSize = Math.min(100, Math.max(1, pageSize));
        List<Object> args = new ArrayList<>();
        String where = "";
        if (!text(status).isBlank()) {
            where = " WHERE status=?";
            args.add(text(status).toUpperCase(Locale.ROOT));
        }
        int total = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM knowledge_document" + where,
                Integer.class,
                args.toArray());
        args.add(safeSize);
        args.add((safePage - 1) * safeSize);
        List<Map<String, Object>> rows = jdbcTemplate.query(
                """
                SELECT document_id, title, file_type, source_name, content_hash, status,
                       version, owner, domain, index_schema_version, effective_start,
                       effective_end, error_message,
                       created_at, updated_at
                FROM knowledge_document
                """ + where + " ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                documentRowMapper(),
                args.toArray());
        return page(total, safePage, safeSize, rows);
    }

    @Override
    public Map<String, Object> listJobs(int pageNo, int pageSize, String status) {
        int safePage = Math.max(1, pageNo);
        int safeSize = Math.min(100, Math.max(1, pageSize));
        List<Object> args = new ArrayList<>();
        String where = "";
        if (!text(status).isBlank()) {
            where = " WHERE j.status=?";
            args.add(text(status).toUpperCase(Locale.ROOT));
        }
        int total = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM knowledge_ingest_job j" + where,
                Integer.class,
                args.toArray());
        args.add(safeSize);
        args.add((safePage - 1) * safeSize);
        List<Map<String, Object>> rows = jdbcTemplate.queryForList(
                """
                SELECT j.job_id, j.document_id, d.title, j.status, j.stage, j.progress,
                       j.chunk_count, j.error_message, j.created_at, j.updated_at
                FROM knowledge_ingest_job j
                JOIN knowledge_document d ON d.document_id=j.document_id
                """ + where + " ORDER BY j.updated_at DESC LIMIT ? OFFSET ?",
                args.toArray());
        return page(total, safePage, safeSize, camelRows(rows));
    }

    @Override
    public Map<String, Object> listFaqCandidates(
            int pageNo, int pageSize, String status) {
        int safePage = Math.max(1, pageNo);
        int safeSize = Math.min(100, Math.max(1, pageSize));
        List<Object> args = new ArrayList<>();
        String where = "";
        if (!text(status).isBlank()) {
            where = " WHERE status=?";
            args.add(text(status).toUpperCase(Locale.ROOT));
        }
        int total = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM faq_candidate" + where,
                Integer.class,
                args.toArray());
        args.add(safeSize);
        args.add((safePage - 1) * safeSize);
        List<Map<String, Object>> rows = jdbcTemplate.queryForList(
                """
                SELECT candidate_id, question, answer, category, source_message_id,
                       frequency, status, reviewer, review_remark, created_at, updated_at
                FROM faq_candidate
                """ + where + " ORDER BY frequency DESC, created_at ASC LIMIT ? OFFSET ?",
                args.toArray());
        return page(total, safePage, safeSize, camelRows(rows));
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> reviewFaqCandidate(
            long candidateId,
            boolean approved,
            String reviewer,
            String remark,
            String correctedAnswer,
            String category) {
        List<Map<String, Object>> rows = jdbcTemplate.queryForList(
                "SELECT * FROM faq_candidate WHERE candidate_id=? FOR UPDATE", candidateId);
        if (rows.isEmpty()) {
            throw new BusinessException("FAQ候选不存在");
        }
        Map<String, Object> candidate = rows.get(0);
        if (!"PENDING".equals(candidate.get("status"))) {
            throw new BusinessException("FAQ候选已经审核");
        }
        if (!approved) {
            jdbcTemplate.update(
                    """
                    UPDATE faq_candidate
                    SET status='REJECTED', reviewer=?, review_remark=?, updated_at=NOW()
                    WHERE candidate_id=?
                    """,
                    text(reviewer), text(remark), candidateId);
            return Map.of("candidateId", candidateId, "status", "REJECTED");
        }

        String answer = text(correctedAnswer).isBlank()
                ? text(candidate.get("answer")) : text(correctedAnswer);
        if (answer.isBlank()) {
            throw new BusinessException("FAQ候选答案不能为空");
        }
        String resolvedCategory = text(category).isBlank()
                ? text(candidate.get("category")) : text(category);
        if (resolvedCategory.isBlank()) {
            resolvedCategory = "general";
        }
        candidate.put("answer", answer);
        candidate.put("category", resolvedCategory);
        jdbcTemplate.update(
                """
                UPDATE faq_candidate
                SET answer=?, category=?, updated_at=NOW()
                WHERE candidate_id=?
                """,
                answer, resolvedCategory, candidateId);

        int questionId = insertFaq(candidate, reviewer);
        jdbcTemplate.update(
                """
                UPDATE faq_candidate
                SET status='APPROVED', reviewer=?, review_remark=?, updated_at=NOW()
                WHERE candidate_id=?
                """,
                text(reviewer), text(remark), candidateId);
        RagDataDTO dto = new RagDataDTO(
                String.valueOf(questionId), RagDataTypeEnum.FAQ.getType());
        transactionalMqSender.sendAfterCommit(
                RabbitMQConfig.RAG_EXCHANGE,
                RabbitMQConfig.RAG_QUEUE_KEY,
                dto,
                MqIdempotencyKeys.ragFaq(String.valueOf(questionId), dto.getVersion()),
                MessageReliabilityLevelEnum.HIGH);
        // FAQ 与文档发布共享同一条数据库版本行锁；Redis 只是永久提示键，
        // 广播失败不能把已经提交的 FAQ 伪装成发布失败。
        long currentVersion = lockReleaseVersion();
        long version = advanceReleaseVersion(currentVersion);
        snapshotCurrentRelease(
                version,
                "publish-faq-" + questionId,
                reviewer,
                null);
        registerAfterCommit(() -> publishVersionBestEffort(version, "FAQ发布"));
        return Map.of(
                "candidateId", candidateId,
                "questionId", questionId,
                "status", "APPROVED");
    }

    @Override
    public Map<String, Object> exactFaq(
            String question, String language, String channel) {
        String normalized = normalizeQuestion(question);
        if (normalized.isBlank()) {
            return Map.of();
        }
        List<Map<String, Object>> rows = jdbcTemplate.queryForList(
                """
                SELECT question_id, question, answer, category, language, channel,
                       priority, version, source, owner
                FROM rag_question
                WHERE publish_status='PUBLISHED'
                  AND (normalized_question=? OR question=?)
                  AND (language=? OR language='all')
                  AND (channel=? OR channel='all')
                  AND (effective_start IS NULL OR effective_start<=NOW())
                  AND (effective_end IS NULL OR effective_end>NOW())
                ORDER BY priority DESC, version DESC
                LIMIT 1
                """,
                normalized,
                text(question),
                text(language).isBlank() ? "zh-CN" : text(language),
                text(channel).isBlank() ? "web" : text(channel));
        if (rows.isEmpty()) {
            return Map.of();
        }
        Map<String, Object> row = rows.get(0);
        jdbcTemplate.update(
                "UPDATE rag_question SET hit_count=hit_count+1 WHERE question_id=?",
                row.get("question_id"));
        return camelRow(row);
    }

    @Override
    public void submitFaqCandidate(
            String question, String answer, Integer sourceMessageId, String category) {
        String normalized = normalizeQuestion(question);
        if (normalized.isBlank() || text(answer).isBlank()) {
            throw new BusinessException("FAQ候选问题和答案不能为空");
        }
        String hash = sha256(normalized);
        jdbcTemplate.update(
                """
                INSERT INTO faq_candidate
                    (question, normalized_hash, answer, category, source_message_id,
                     frequency, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, 'PENDING', NOW(), NOW())
                ON DUPLICATE KEY UPDATE
                    frequency=frequency+1,
                    answer=VALUES(answer),
                    source_message_id=COALESCE(VALUES(source_message_id), source_message_id),
                    updated_at=NOW()
                """,
                text(question), hash, text(answer),
                text(category).isBlank() ? "general" : text(category),
                sourceMessageId);
    }

    @Override
    public long releaseVersion() {
        Long version = jdbcTemplate.queryForObject(
                "SELECT current_version FROM knowledge_release WHERE release_key='global'",
                Long.class);
        return version == null ? 1L : version;
    }

    @Override
    @Transactional(readOnly = true)
    public Map<String, Object> releaseCatalog() {
        return releaseCatalog(null);
    }

    @Override
    @Transactional(readOnly = true)
    public Map<String, Object> releaseCatalog(Long requestedReleaseVersion) {
        // Reading the version first establishes the REPEATABLE READ snapshot used
        // by both metadata and membership queries.
        long currentVersion = releaseVersion();
        long version = requestedReleaseVersion == null
                ? currentVersion : requestedReleaseVersion;
        if (version < 1 || version > currentVersion) {
            throw new BusinessException("知识发布快照不存在: " + version);
        }
        List<Map<String, Object>> snapshots = jdbcTemplate.queryForList(
                """
                SELECT release_version, release_name, catalog_sha256,
                       source_release_version, activated_by, created_at
                FROM knowledge_release_snapshot
                WHERE release_version=?
                """,
                version);
        if (snapshots.isEmpty()) {
            if (requestedReleaseVersion != null) {
                throw new BusinessException("知识发布快照不存在: " + version);
            }
            return legacyCurrentCatalog(version);
        }
        List<Map<String, Object>> rawDocuments = loadReleaseDocuments(version);
        List<Map<String, Object>> documents = camelRows(rawDocuments);
        List<String> activeDocumentIds = rawDocuments.stream()
                .map(row -> String.valueOf(row.get("document_id")))
                .toList();
        Map<String, Object> snapshot = snapshots.get(0);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("version", version);
        result.put("releaseName", snapshot.get("release_name"));
        result.put("catalogSha256", snapshot.get("catalog_sha256"));
        result.put("sourceReleaseVersion", snapshot.get("source_release_version"));
        result.put("activeDocumentIds", activeDocumentIds);
        result.put("documents", documents);
        return result;
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> activateRelease(
            String releaseName,
            String catalogSha256,
            List<Long> documentIds,
            Long sourceReleaseVersion,
            String owner) {
        String name = text(releaseName);
        if (name.isBlank() || name.length() > 100) {
            throw new BusinessException("releaseName不能为空且不能超过100字符");
        }
        Set<Long> selectedIds = new LinkedHashSet<>();
        for (Long documentId : documentIds == null ? List.<Long>of() : documentIds) {
            if (documentId == null || documentId < 1) {
                throw new BusinessException("documentIds包含无效文档ID");
            }
            selectedIds.add(documentId);
        }
        boolean copiesSource = sourceReleaseVersion != null;
        if (copiesSource == !selectedIds.isEmpty()) {
            throw new BusinessException(
                    "sourceReleaseVersion与documentIds必须且只能提供一个");
        }

        long currentVersion = lockReleaseVersion();
        List<Map<String, Object>> documents;
        String resolvedCatalogSha;
        if (copiesSource) {
            if (sourceReleaseVersion < 1 || sourceReleaseVersion > currentVersion) {
                throw new BusinessException("源知识发布快照不存在");
            }
            List<Map<String, Object>> sourceSnapshots = jdbcTemplate.queryForList(
                    """
                    SELECT catalog_sha256
                    FROM knowledge_release_snapshot
                    WHERE release_version=?
                    """,
                    sourceReleaseVersion);
            if (sourceSnapshots.isEmpty()) {
                throw new BusinessException("源知识发布快照不存在");
            }
            documents = loadReleaseDocuments(sourceReleaseVersion);
            resolvedCatalogSha = String.valueOf(
                    sourceSnapshots.get(0).get("catalog_sha256"));
            if (!text(catalogSha256).isBlank()
                    && !resolvedCatalogSha.equalsIgnoreCase(text(catalogSha256))) {
                throw new BusinessException("catalogSha256与源快照不一致");
            }
        } else {
            String requestedCatalogSha = normalizeCatalogSha(catalogSha256);
            documents = loadSelectableDocuments(selectedIds);
            if (documents.size() != selectedIds.size()) {
                throw new BusinessException("发布集合包含不存在或尚未索引的知识文档");
            }
            resolvedCatalogSha = catalogMembershipSha(documents);
            if (!resolvedCatalogSha.equals(requestedCatalogSha)) {
                throw new BusinessException("catalogSha256与所选知识文档集合不一致");
            }
        }
        long nextVersion = currentVersion + 1;
        applyActiveMembership(documents);
        insertReleaseSnapshot(
                nextVersion,
                name,
                resolvedCatalogSha,
                sourceReleaseVersion,
                owner,
                documents);
        advanceReleaseVersion(currentVersion);
        registerAfterCommit(() -> publishVersionBestEffort(
                nextVersion,
                copiesSource ? "知识快照回滚" : "知识快照激活"));
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("releaseVersion", nextVersion);
        result.put("releaseName", name);
        result.put("catalogSha256", resolvedCatalogSha);
        result.put("sourceReleaseVersion", sourceReleaseVersion);
        result.put("activeDocumentIds", documents.stream()
                .map(row -> String.valueOf(row.get("document_id")))
                .toList());
        return result;
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public long invalidateCaches() {
        long currentVersion = lockReleaseVersion();
        long version = advanceReleaseVersion(currentVersion);
        snapshotCurrentRelease(version, "manual-cache-invalidation", "system", null);
        registerAfterCommit(() -> publishVersionBestEffort(version, "手工缓存失效"));
        return version;
    }

    @Override
    public List<Map<String, Object>> topFaq(int limit) {
        int safeLimit = Math.min(100, Math.max(1, limit));
        return camelRows(jdbcTemplate.queryForList(
                """
                SELECT question_id, question, answer, category, language, channel,
                       priority, version, source, owner, hit_count
                FROM rag_question
                WHERE publish_status='PUBLISHED'
                  AND (effective_start IS NULL OR effective_start<=NOW())
                  AND (effective_end IS NULL OR effective_end>NOW())
                ORDER BY hit_count DESC, priority DESC, update_time DESC
                LIMIT ?
                """,
                safeLimit));
    }

    private long insertDocument(
            String title,
            ParsedDocument parsed,
            String hash,
            String owner,
            String domain) {
        KeyHolder keyHolder = new GeneratedKeyHolder();
        jdbcTemplate.update(connection -> {
            PreparedStatement statement = connection.prepareStatement(
                    """
                    INSERT INTO knowledge_document
                        (title, file_type, source_name, content_hash, normalized_text,
                         status, version, owner, domain, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'PARSING', 1, ?, ?, NOW(), NOW())
                    """,
                    Statement.RETURN_GENERATED_KEYS);
            statement.setString(1, title);
            statement.setString(2, parsed.fileType());
            statement.setString(3, parsed.sourceName());
            statement.setString(4, hash);
            statement.setString(5, parsed.normalizedText());
            statement.setString(6, text(owner));
            statement.setString(7, domain);
            return statement;
        }, keyHolder);
        Number key = keyHolder.getKey();
        if (key == null) {
            throw new BusinessException("知识文档保存失败");
        }
        return key.longValue();
    }

    private long insertJob(long documentId, String status, String stage, int progress) {
        KeyHolder keyHolder = new GeneratedKeyHolder();
        jdbcTemplate.update(connection -> {
            PreparedStatement statement = connection.prepareStatement(
                    """
                    INSERT INTO knowledge_ingest_job
                        (document_id, status, stage, progress, chunk_count,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, 0, NOW(), NOW())
                    """,
                    Statement.RETURN_GENERATED_KEYS);
            statement.setLong(1, documentId);
            statement.setString(2, status);
            statement.setString(3, stage);
            statement.setInt(4, progress);
            return statement;
        }, keyHolder);
        return keyHolder.getKey() == null ? 0L : keyHolder.getKey().longValue();
    }

    private void insertChunks(
            long documentId,
            int version,
            List<Chunk> chunks,
            String title,
            String sourceName,
            String domain) {
        List<Object[]> args = new ArrayList<>();
        for (Chunk chunk : chunks) {
            String chunkId = "knowledge_" + documentId + "_" + version + "_" + chunk.index();
            Map<String, Object> metadata = Map.of(
                    "title", title,
                    "source", sourceName,
                    "heading", chunk.heading(),
                    "domain", domain);
            args.add(new Object[] {
                    chunkId,
                    documentId,
                    chunk.index(),
                    chunk.heading(),
                    chunk.content(),
                    json(metadata),
                    chunk.tokenCount(),
                    version
            });
        }
        jdbcTemplate.batchUpdate(
                """
                INSERT INTO knowledge_chunk
                    (chunk_id, document_id, chunk_index, heading, content,
                     metadata_json, token_count, version, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', NOW(), NOW())
                """,
                args);
    }

    private int insertFaq(Map<String, Object> candidate, String reviewer) {
        KeyHolder keyHolder = new GeneratedKeyHolder();
        jdbcTemplate.update(connection -> {
            PreparedStatement statement = connection.prepareStatement(
                    """
                    INSERT INTO rag_question
                        (question, normalized_question, similar_question, answer, create_time,
                         category, language, channel, priority, version, publish_status,
                         source, owner, hit_count, update_time)
                    VALUES (?, ?, NULL, ?, NOW(), ?, 'zh-CN', 'all', 0, 1,
                            'PUBLISHED', 'FAQ_CANDIDATE', ?, 0, NOW())
                    """,
                    Statement.RETURN_GENERATED_KEYS);
            String question = String.valueOf(candidate.get("question"));
            statement.setString(1, question);
            statement.setString(2, normalizeQuestion(question));
            statement.setString(3, String.valueOf(candidate.get("answer")));
            statement.setString(4, String.valueOf(candidate.get("category")));
            statement.setString(5, text(reviewer));
            return statement;
        }, keyHolder);
        if (keyHolder.getKey() == null) {
            throw new BusinessException("FAQ发布失败");
        }
        return keyHolder.getKey().intValue();
    }

    private Map<String, Object> getDocument(long documentId) {
        List<Map<String, Object>> rows = jdbcTemplate.query(
                """
                SELECT document_id, title, file_type, source_name, content_hash, status,
                       version, owner, domain, index_schema_version, effective_start,
                       effective_end, error_message,
                       created_at, updated_at
                FROM knowledge_document WHERE document_id=?
                """,
                documentRowMapper(),
                documentId);
        return rows.isEmpty() ? Map.of() : rows.get(0);
    }

    private Map<String, Object> requireDocument(long documentId) {
        Map<String, Object> result = getDocument(documentId);
        if (result.isEmpty()) {
            throw new BusinessException("知识文档不存在");
        }
        return result;
    }

    private Map<String, Object> requireDocumentForUpdate(long documentId) {
        List<Map<String, Object>> rows = jdbcTemplate.query(
                """
                SELECT document_id, title, file_type, source_name, content_hash, status,
                       version, owner, domain, index_schema_version, effective_start,
                       effective_end, error_message,
                       created_at, updated_at
                FROM knowledge_document WHERE document_id=? FOR UPDATE
                """,
                documentRowMapper(),
                documentId);
        if (rows.isEmpty()) {
            throw new BusinessException("知识文档不存在");
        }
        return rows.get(0);
    }

    private RowMapper<Map<String, Object>> documentRowMapper() {
        return (rs, rowNum) -> {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("documentId", rs.getLong("document_id"));
            row.put("title", rs.getString("title"));
            row.put("fileType", rs.getString("file_type"));
            row.put("sourceName", rs.getString("source_name"));
            row.put("contentHash", rs.getString("content_hash"));
            row.put("status", rs.getString("status"));
            row.put("version", rs.getInt("version"));
            row.put("owner", rs.getString("owner"));
            row.put("domain", rs.getString("domain"));
            row.put("indexSchemaVersion", rs.getInt("index_schema_version"));
            row.put("effectiveStart", time(rs.getObject("effective_start", LocalDateTime.class)));
            row.put("effectiveEnd", time(rs.getObject("effective_end", LocalDateTime.class)));
            row.put("errorMessage", rs.getString("error_message"));
            row.put("createdAt", time(rs.getObject("created_at", LocalDateTime.class)));
            row.put("updatedAt", time(rs.getObject("updated_at", LocalDateTime.class)));
            return row;
        };
    }

    private void markJobFailed(long jobId, long documentId, Exception e) {
        String error = text(e.getMessage());
        if (error.length() > 500) {
            error = error.substring(0, 500);
        }
        jdbcTemplate.update(
                """
                UPDATE knowledge_ingest_job
                SET status='FAILED', stage='FAILED', error_message=?, updated_at=NOW()
                WHERE job_id=?
                """,
                error, jobId);
        jdbcTemplate.update(
                """
                UPDATE knowledge_document
                SET status='ERROR', error_message=?, updated_at=NOW()
                WHERE document_id=?
                """,
                error, documentId);
    }

    private void markReindexJobFailed(long jobId, Exception e) {
        String error = text(e.getMessage());
        if (error.length() > 500) {
            error = error.substring(0, 500);
        }
        jdbcTemplate.update(
                """
                UPDATE knowledge_ingest_job
                SET status='FAILED', stage='REINDEX_FAILED', error_message=?, updated_at=NOW()
                WHERE job_id=?
                """,
                error, jobId);
    }

    private Map<String, Object> knowledgeMetadata(
            Map<String, Object> document,
            Map<String, Object> chunk,
            long releaseVersion,
            String originalContent) {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("dataType", "knowledge");
        metadata.put("documentId", String.valueOf(document.get("documentId")));
        metadata.put("title", document.get("title"));
        metadata.put("heading", chunk.get("heading"));
        metadata.put("source", document.get("sourceName"));
        metadata.put("domain", document.get("domain"));
        metadata.put("version", releaseVersion);
        metadata.put("status", "PUBLISHED");
        metadata.put("originalContent", originalContent);
        metadata.put("contextEnriched", false);
        metadata.put("indexSchemaVersion", INDEX_SCHEMA_VERSION);
        return metadata;
    }

    private long lockReleaseVersion() {
        jdbcTemplate.update(
                """
                INSERT INTO knowledge_release (release_key, current_version, updated_at)
                VALUES ('global', 1, NOW())
                ON DUPLICATE KEY UPDATE release_key=VALUES(release_key)
                """);
        Long version = jdbcTemplate.queryForObject(
                """
                SELECT current_version
                FROM knowledge_release
                WHERE release_key='global'
                FOR UPDATE
                """,
                Long.class);
        if (version == null) {
            throw new IllegalStateException("知识发布版本行不存在");
        }
        return version;
    }

    private long advanceReleaseVersion(long expectedVersion) {
        long nextVersion = expectedVersion + 1;
        int updated = jdbcTemplate.update(
                """
                UPDATE knowledge_release
                SET current_version=?, updated_at=NOW()
                WHERE release_key='global' AND current_version=?
                """,
                nextVersion, expectedVersion);
        if (updated != 1) {
            throw new IllegalStateException(
                    "知识发布版本推进冲突, expectedVersion=" + expectedVersion);
        }
        return nextVersion;
    }

    private Map<String, Object> legacyCurrentCatalog(long version) {
        List<String> activeDocumentIds = jdbcTemplate.queryForList(
                        """
                        SELECT document_id
                        FROM knowledge_document
                        WHERE status='PUBLISHED'
                        ORDER BY document_id
                        """,
                        Long.class)
                .stream()
                .map(String::valueOf)
                .toList();
        List<Map<String, Object>> rawDocuments = loadCurrentPublishedDocuments();
        List<Map<String, Object>> documents = camelRows(rawDocuments);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("version", version);
        result.put("releaseName", "legacy-current");
        result.put("catalogSha256", catalogMembershipSha(rawDocuments));
        result.put("sourceReleaseVersion", null);
        result.put("activeDocumentIds", activeDocumentIds);
        result.put("documents", documents);
        return result;
    }

    private List<Map<String, Object>> loadCurrentPublishedDocuments() {
        return jdbcTemplate.queryForList(
                """
                SELECT d.document_id, d.source_name, d.content_hash, d.version,
                       d.domain, d.index_schema_version,
                       COUNT(c.chunk_id) AS chunk_count
                FROM knowledge_document d
                LEFT JOIN knowledge_chunk c
                  ON c.document_id=d.document_id
                 AND c.version=d.version
                 AND c.status='PUBLISHED'
                WHERE d.status='PUBLISHED'
                GROUP BY d.document_id, d.source_name, d.content_hash, d.version,
                         d.domain, d.index_schema_version
                ORDER BY d.document_id
                """);
    }

    private List<Map<String, Object>> loadReleaseDocuments(long releaseVersion) {
        return jdbcTemplate.queryForList(
                """
                SELECT document_id, source_name, content_hash,
                       document_version AS version, domain, index_schema_version,
                       chunk_count
                FROM knowledge_release_document
                WHERE release_version=?
                ORDER BY document_id
                """,
                releaseVersion);
    }

    private List<Map<String, Object>> loadSelectableDocuments(Set<Long> documentIds) {
        String placeholders = String.join(",", documentIds.stream()
                .map(ignored -> "?")
                .toList());
        return jdbcTemplate.queryForList(
                """
                SELECT d.document_id, d.source_name, d.content_hash, d.version,
                       d.domain, d.index_schema_version,
                       COUNT(c.chunk_id) AS chunk_count
                FROM knowledge_document d
                JOIN knowledge_chunk c
                  ON c.document_id=d.document_id
                 AND c.version=d.version
                 AND c.status IN ('PUBLISHED','ARCHIVED')
                WHERE d.document_id IN (%s)
                  AND d.index_schema_version > 0
                GROUP BY d.document_id, d.source_name, d.content_hash, d.version,
                         d.domain, d.index_schema_version
                HAVING COUNT(c.chunk_id) > 0
                ORDER BY d.document_id
                """.formatted(placeholders),
                documentIds.toArray());
    }

    private void snapshotCurrentRelease(
            long releaseVersion,
            String releaseName,
            String owner,
            Long sourceReleaseVersion) {
        List<Map<String, Object>> documents = loadCurrentPublishedDocuments();
        insertReleaseSnapshot(
                releaseVersion,
                releaseName,
                catalogMembershipSha(documents),
                sourceReleaseVersion,
                owner,
                documents);
    }

    private void applyActiveMembership(List<Map<String, Object>> documents) {
        List<Object> documentIds = documents.stream()
                .map(row -> row.get("document_id"))
                .toList();
        if (documentIds.isEmpty()) {
            jdbcTemplate.update(
                    "UPDATE knowledge_chunk SET status='ARCHIVED', updated_at=NOW() "
                            + "WHERE status='PUBLISHED'");
            jdbcTemplate.update(
                    "UPDATE knowledge_document SET status='ARCHIVED', updated_at=NOW() "
                            + "WHERE status='PUBLISHED'");
            return;
        }
        String placeholders = String.join(",", documentIds.stream()
                .map(ignored -> "?")
                .toList());
        jdbcTemplate.update(
                ("""
                UPDATE knowledge_chunk c
                JOIN knowledge_document d ON d.document_id=c.document_id
                SET c.status='ARCHIVED', c.updated_at=NOW()
                WHERE d.status='PUBLISHED' AND d.document_id NOT IN (%s)
                """).formatted(placeholders),
                documentIds.toArray());
        jdbcTemplate.update(
                ("""
                UPDATE knowledge_document
                SET status='ARCHIVED', updated_at=NOW()
                WHERE status='PUBLISHED' AND document_id NOT IN (%s)
                """).formatted(placeholders),
                documentIds.toArray());
        jdbcTemplate.update(
                ("""
                UPDATE knowledge_document
                SET status='PUBLISHED', updated_at=NOW()
                WHERE document_id IN (%s)
                """).formatted(placeholders),
                documentIds.toArray());
        jdbcTemplate.update(
                ("""
                UPDATE knowledge_chunk
                SET status='PUBLISHED', updated_at=NOW()
                WHERE document_id IN (%s)
                """).formatted(placeholders),
                documentIds.toArray());
    }

    private void insertReleaseSnapshot(
            long releaseVersion,
            String releaseName,
            String catalogSha256,
            Long sourceReleaseVersion,
            String owner,
            List<Map<String, Object>> documents) {
        jdbcTemplate.update(
                """
                INSERT INTO knowledge_release_snapshot
                    (release_version, release_name, catalog_sha256,
                     source_release_version, activated_by, created_at)
                VALUES (?, ?, ?, ?, ?, NOW(3))
                """,
                releaseVersion,
                releaseName,
                catalogSha256,
                sourceReleaseVersion,
                text(owner));
        if (documents.isEmpty()) {
            return;
        }
        List<Object[]> rows = new ArrayList<>();
        for (Map<String, Object> document : documents) {
            rows.add(new Object[] {
                    document.get("document_id"),
                    number(document.get("version"), 1),
                    text(document.get("source_name")),
                    text(document.get("content_hash")),
                    text(document.get("domain")),
                    number(document.get("index_schema_version"), 0),
                    number(document.get("chunk_count"), 0),
                    releaseVersion
            });
        }
        jdbcTemplate.batchUpdate(
                """
                INSERT INTO knowledge_release_document
                    (document_id, document_version, source_name, content_hash,
                     domain, index_schema_version, chunk_count, release_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows);
    }

    private String catalogMembershipSha(List<Map<String, Object>> documents) {
        StringBuilder canonical = new StringBuilder();
        for (Map<String, Object> document : documents) {
            canonical.append(document.get("document_id")).append('|')
                    .append(document.get("version")).append('|')
                    .append(document.get("source_name")).append('|')
                    .append(document.get("content_hash")).append('|')
                    .append(document.get("domain")).append('|')
                    .append(document.get("index_schema_version")).append('|')
                    .append(document.get("chunk_count")).append('\n');
        }
        return sha256(canonical.toString());
    }

    private String normalizeCatalogSha(String value) {
        String normalized = text(value).toLowerCase(Locale.ROOT);
        if (!normalized.matches("[0-9a-f]{64}")) {
            throw new BusinessException("catalogSha256必须是64位SHA-256");
        }
        return normalized;
    }

    private void registerAfterCommit(Runnable action) {
        if (!TransactionSynchronizationManager.isSynchronizationActive()) {
            action.run();
            return;
        }
        TransactionSynchronizationManager.registerSynchronization(
                new TransactionSynchronization() {
                    @Override
                    public void afterCommit() {
                        action.run();
                    }
                });
    }

    private void publishVersionBestEffort(long version, String operation) {
        try {
            publishVersionToRedis(version);
        } catch (RuntimeException e) {
            log.warn("{}版本广播失败，数据库目录仍为权威来源, version={}", operation, version, e);
        }
    }

    /** 把版本号广播给检索端（永久 Redis hint + pubsub）。必须在事务提交后调用。 */
    private void publishVersionToRedis(long version) {
        try {
            stringRedisTemplate.opsForValue().set(RELEASE_KEY, String.valueOf(version));
            stringRedisTemplate.convertAndSend(RELEASE_TOPIC, String.valueOf(version));
        } catch (RuntimeException e) {
            throw new IllegalStateException(
                    "知识版本Redis广播失败，数据库版本已生效, version=" + version, e);
        }
    }

    private Map<String, Object> page(
            int total, int pageNo, int pageSize, List<Map<String, Object>> rows) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("totalCount", total);
        result.put("pageNo", pageNo);
        result.put("pageSize", pageSize);
        result.put("pageTotal", total == 0 ? 0 : (total + pageSize - 1) / pageSize);
        result.put("list", rows);
        return result;
    }

    private List<Map<String, Object>> camelRows(List<Map<String, Object>> rows) {
        return rows.stream().map(this::camelRow).toList();
    }

    private Map<String, Object> camelRow(Map<String, Object> row) {
        Map<String, Object> result = new LinkedHashMap<>();
        row.forEach((key, value) -> result.put(camel(key), value));
        return result;
    }

    private String camel(String value) {
        StringBuilder result = new StringBuilder();
        boolean upper = false;
        for (char c : value.toCharArray()) {
            if (c == '_') {
                upper = true;
            } else if (upper) {
                result.append(Character.toUpperCase(c));
                upper = false;
            } else {
                result.append(c);
            }
        }
        return result.toString();
    }

    private String json(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            throw new BusinessException("知识元数据序列化失败", e);
        }
    }

    private String normalizeQuestion(String value) {
        return text(value)
                .toLowerCase(Locale.ROOT)
                .replaceAll("[\\s，。！？、,.!?;；:：\"'（）()\\[\\]【】]+", "");
    }

    private String sha256(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(
                    value.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            throw new BusinessException("摘要计算失败", e);
        }
    }

    private String stripExtension(String name) {
        int index = name.lastIndexOf('.');
        return index > 0 ? name.substring(0, index) : name;
    }

    private String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private String normalizeDomain(String value) {
        String domain = text(value).toUpperCase(Locale.ROOT);
        if (domain.isBlank()) {
            return "GENERAL";
        }
        if (!domain.matches("[A-Z][A-Z0-9_]{0,63}")) {
            throw new BusinessException("知识领域只能包含大写字母、数字和下划线");
        }
        return domain;
    }

    private int number(Object value, int fallback) {
        if (value instanceof Number number) {
            return number.intValue();
        }
        try {
            return Integer.parseInt(text(value));
        } catch (NumberFormatException e) {
            return fallback;
        }
    }

    private String time(LocalDateTime value) {
        return value == null ? null : TIME_FORMATTER.format(value);
    }

    private record EnrichmentCandidate(String title, Document document) {
    }
}
