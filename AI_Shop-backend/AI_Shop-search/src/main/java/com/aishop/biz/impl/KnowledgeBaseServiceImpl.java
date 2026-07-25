package com.aishop.biz.impl;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.aishop.biz.KnowledgeBaseService;
import com.aishop.component.KnowledgeDocumentParser;
import com.aishop.component.KnowledgeDocumentParser.Chunk;
import com.aishop.component.KnowledgeDocumentParser.ParsedDocument;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.ReliableMessageSender;
import com.aishop.entity.dto.RagDataDTO;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.enums.RagDataTypeEnum;
import com.aishop.exception.BusinessException;
import com.aishop.support.MqIdempotencyKeys;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.ai.document.Document;
import org.springframework.ai.vectorstore.VectorStore;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
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
import java.util.List;
import java.util.Locale;
import java.util.Map;

@Service
@Slf4j
public class KnowledgeBaseServiceImpl implements KnowledgeBaseService {

    private static final String RELEASE_KEY = "mall:knowledge:version";
    private static final String RELEASE_TOPIC = "knowledge.release";
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
    private ReliableMessageSender reliableMessageSender;

    @Override
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> upload(MultipartFile file, String title, String owner) {
        ParsedDocument parsed = documentParser.parse(file);
        String resolvedTitle = text(title).isBlank()
                ? stripExtension(parsed.sourceName()) : text(title);
        String hash = sha256(parsed.normalizedText());
        List<Map<String, Object>> existing = jdbcTemplate.queryForList(
                "SELECT document_id FROM knowledge_document WHERE content_hash=? LIMIT 1", hash);
        if (!existing.isEmpty()) {
            return getDocument(((Number) existing.get(0).get("document_id")).longValue());
        }

        long documentId = insertDocument(resolvedTitle, parsed, hash, owner);
        long jobId = insertJob(documentId, "RUNNING", "CHUNK", 30);
        try {
            insertChunks(documentId, 1, parsed.chunks(), resolvedTitle, parsed.sourceName());
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
    public Map<String, Object> publish(long documentId, String owner) {
        Map<String, Object> document = requireDocument(documentId);
        if ("ARCHIVED".equals(document.get("status"))) {
            throw new BusinessException("归档文档不能发布");
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

        long jobId = insertJob(documentId, "RUNNING", "INDEX", 85);
        try {
            List<Document> batch = new ArrayList<>();
            for (Map<String, Object> chunk : chunks) {
                Map<String, Object> metadata = new LinkedHashMap<>();
                metadata.put("dataType", "knowledge");
                metadata.put("documentId", String.valueOf(documentId));
                metadata.put("title", document.get("title"));
                metadata.put("heading", chunk.get("heading"));
                metadata.put("source", document.get("source_name"));
                metadata.put("version", version);
                metadata.put("status", "PUBLISHED");
                batch.add(new Document(
                        String.valueOf(chunk.get("chunk_id")),
                        String.valueOf(chunk.get("content")),
                        metadata));
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
                    SET status='PUBLISHED', owner=COALESCE(NULLIF(?, ''), owner), updated_at=NOW()
                    WHERE document_id=?
                    """,
                    text(owner), documentId);
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
            long releaseVersion = bumpReleaseVersion();
            Map<String, Object> result = getDocument(documentId);
            result.put("releaseVersion", releaseVersion);
            return result;
        } catch (RuntimeException e) {
            markJobFailed(jobId, documentId, e);
            throw new BusinessException("知识文档发布失败：" + e.getMessage(), e);
        }
    }

    @Override
    public Map<String, Object> archive(long documentId) {
        Map<String, Object> document = requireDocument(documentId);
        List<String> ids = jdbcTemplate.queryForList(
                "SELECT chunk_id FROM knowledge_chunk WHERE document_id=?",
                String.class, documentId);
        if (!ids.isEmpty()) {
            vectorStore.delete(ids);
        }
        jdbcTemplate.update(
                "UPDATE knowledge_document SET status='ARCHIVED', updated_at=NOW() WHERE document_id=?",
                documentId);
        jdbcTemplate.update(
                "UPDATE knowledge_chunk SET status='ARCHIVED', updated_at=NOW() WHERE document_id=?",
                documentId);
        long version = bumpReleaseVersion();
        Map<String, Object> result = new LinkedHashMap<>(document);
        result.put("status", "ARCHIVED");
        result.put("releaseVersion", version);
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
                       version, owner, effective_start, effective_end, error_message,
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
        reliableMessageSender.sendMessage(
                RabbitMQConfig.RAG_EXCHANGE,
                RabbitMQConfig.RAG_QUEUE_KEY,
                dto,
                MqIdempotencyKeys.ragFaq(String.valueOf(questionId), dto.getVersion()),
                MessageReliabilityLevelEnum.HIGH);
        bumpReleaseVersion();
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
    public long invalidateCaches() {
        return bumpReleaseVersion();
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
            String title, ParsedDocument parsed, String hash, String owner) {
        KeyHolder keyHolder = new GeneratedKeyHolder();
        jdbcTemplate.update(connection -> {
            PreparedStatement statement = connection.prepareStatement(
                    """
                    INSERT INTO knowledge_document
                        (title, file_type, source_name, content_hash, normalized_text,
                         status, version, owner, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'PARSING', 1, ?, NOW(), NOW())
                    """,
                    Statement.RETURN_GENERATED_KEYS);
            statement.setString(1, title);
            statement.setString(2, parsed.fileType());
            statement.setString(3, parsed.sourceName());
            statement.setString(4, hash);
            statement.setString(5, parsed.normalizedText());
            statement.setString(6, text(owner));
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
            String sourceName) {
        List<Object[]> args = new ArrayList<>();
        for (Chunk chunk : chunks) {
            String chunkId = "knowledge_" + documentId + "_" + version + "_" + chunk.index();
            Map<String, Object> metadata = Map.of(
                    "title", title,
                    "source", sourceName,
                    "heading", chunk.heading());
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
                       version, owner, effective_start, effective_end, error_message,
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

    private synchronized long bumpReleaseVersion() {
        jdbcTemplate.update(
                """
                INSERT INTO knowledge_release (release_key, current_version, updated_at)
                VALUES ('global', 2, NOW())
                ON DUPLICATE KEY UPDATE current_version=current_version+1, updated_at=NOW()
                """);
        long version = releaseVersion();
        try {
            stringRedisTemplate.opsForValue().set(RELEASE_KEY, String.valueOf(version));
            stringRedisTemplate.convertAndSend(RELEASE_TOPIC, String.valueOf(version));
        } catch (RuntimeException e) {
            log.warn("知识版本Redis广播失败，数据库版本仍有效, version={}", version, e);
        }
        return version;
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
}
