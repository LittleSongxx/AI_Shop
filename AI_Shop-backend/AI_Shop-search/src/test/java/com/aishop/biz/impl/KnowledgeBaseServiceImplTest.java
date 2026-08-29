package com.aishop.biz.impl;

import com.aishop.component.ContextPrefixEnricher;
import com.aishop.exception.BusinessException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.ai.vectorstore.VectorStore;
import org.springframework.ai.document.Document;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.test.util.ReflectionTestUtils;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class KnowledgeBaseServiceImplTest {

    @Mock
    private JdbcTemplate jdbcTemplate;
    @Mock
    private VectorStore vectorStore;
    @Mock
    private StringRedisTemplate stringRedisTemplate;
    @Mock
    private ValueOperations<String, String> valueOperations;
    @Mock
    private ContextPrefixEnricher contextPrefixEnricher;

    private KnowledgeBaseServiceImpl service;

    @BeforeEach
    void setUp() {
        service = new KnowledgeBaseServiceImpl();
        ReflectionTestUtils.setField(service, "jdbcTemplate", jdbcTemplate);
        ReflectionTestUtils.setField(service, "vectorStore", vectorStore);
        ReflectionTestUtils.setField(service, "stringRedisTemplate", stringRedisTemplate);
        ReflectionTestUtils.setField(service, "contextPrefixEnricher", contextPrefixEnricher);
    }

    @Test
    @SuppressWarnings("unchecked")
    void alreadyPublishedDocumentIsRejectedBeforeAnyVectorWrite() {
        when(jdbcTemplate.query(
                argThat(sql -> sql.contains("knowledge_document") && sql.contains("FOR UPDATE")),
                any(RowMapper.class),
                eq(42L)))
                .thenReturn(List.of(document(42L, "PUBLISHED")));

        assertThrows(BusinessException.class, () -> service.publish(42L, "owner"));

        verify(vectorStore, never()).add(anyList());
    }

    @Test
    @SuppressWarnings("unchecked")
    void deletedDocumentCannotBePublished() {
        when(jdbcTemplate.query(
                argThat(sql -> sql.contains("knowledge_document") && sql.contains("FOR UPDATE")),
                any(RowMapper.class),
                eq(42L)))
                .thenReturn(List.of(document(42L, "DELETED")));

        assertThrows(BusinessException.class, () -> service.publish(42L, "owner"));

        verify(vectorStore, never()).add(anyList());
    }

    @Test
    @SuppressWarnings("unchecked")
    void logicalDeleteIsIdempotentAndRetainsHistoricalVectors() {
        when(jdbcTemplate.query(
                argThat(sql -> sql.contains("knowledge_document") && sql.contains("FOR UPDATE")),
                any(RowMapper.class),
                eq(42L)))
                .thenReturn(List.of(document(42L, "DELETED")));
        when(jdbcTemplate.queryForObject(
                argThat(sql -> sql.contains("current_version")),
                eq(Long.class)))
                .thenReturn(9L);

        Map<String, Object> result = service.delete(42L, "owner");

        assertEquals("DELETED", result.get("status"));
        assertEquals(true, result.get("deleted"));
        assertEquals(9L, result.get("releaseVersion"));
        assertEquals(true, result.get("vectorsRetainedForHistoricalRelease"));
        verify(vectorStore, never()).delete(anyList());
    }

    @Test
    @SuppressWarnings("unchecked")
    void archiveRetainsVectorsAndCreatesANewImmutableSnapshot() {
        when(stringRedisTemplate.opsForValue()).thenReturn(valueOperations);
        when(jdbcTemplate.query(
                argThat(sql -> sql.contains("knowledge_document") && sql.contains("FOR UPDATE")),
                any(RowMapper.class),
                eq(42L)))
                .thenReturn(List.of(document(42L, "PUBLISHED")));
        when(jdbcTemplate.queryForList(
                argThat(sql -> sql.contains("FROM knowledge_document d")
                        && sql.contains("COUNT(c.chunk_id)"))))
                .thenReturn(List.of());
        when(jdbcTemplate.queryForObject(
                argThat(sql -> sql.contains("knowledge_release") && sql.contains("FOR UPDATE")),
                eq(Long.class)))
                .thenReturn(5L);
        lenient().when(jdbcTemplate.update(
                argThat(sql -> sql != null && sql.contains("SET current_version=?")),
                eq(6L),
                eq(5L)))
                .thenReturn(1);
        Map<String, Object> result = service.archive(42L);

        assertEquals("ARCHIVED", result.get("status"));
        assertEquals(6L, result.get("releaseVersion"));
        assertEquals(true, result.get("vectorsRetainedForHistoricalRelease"));
        verify(vectorStore, never()).delete(anyList());
        verify(jdbcTemplate).update(
                argThat(sql -> sql.contains("INSERT INTO knowledge_release_snapshot")),
                eq(6L),
                eq("archive-document-42"),
                any(String.class),
                eq(null),
                eq("system"));
    }

    @Test
    void catalogReadsTheExactImmutableReleaseMembership() {
        when(jdbcTemplate.queryForObject(
                argThat(sql -> sql.contains("current_version")),
                eq(Long.class)))
                .thenReturn(9L);
        when(jdbcTemplate.queryForList(
                argThat(sql -> sql != null && sql.contains("knowledge_release_snapshot")),
                eq(9L)))
                .thenReturn(List.of(Map.of(
                        "release_version", 9L,
                        "release_name", "knowledge-v2",
                        "catalog_sha256", "b".repeat(64),
                        "activated_by", "admin")));
        when(jdbcTemplate.queryForList(
                argThat(sql -> sql != null && sql.contains("knowledge_release_document")),
                eq(9L)))
                .thenReturn(List.of(
                    Map.of(
                        "document_id", 11L,
                        "source_name", "shipping.md",
                        "content_hash", "a".repeat(64),
                        "version", 2,
                        "domain", "LOGISTICS",
                        "index_schema_version", 1,
                        "chunk_count", 7L),
                    Map.of(
                        "document_id", 12L,
                        "source_name", "support.md",
                        "content_hash", "c".repeat(64),
                        "version", 1,
                        "domain", "SUPPORT",
                        "index_schema_version", 1,
                        "chunk_count", 4L)));

        Map<String, Object> catalog = service.releaseCatalog();

        assertEquals(9L, catalog.get("version"));
        assertEquals("knowledge-v2", catalog.get("releaseName"));
        assertEquals("b".repeat(64), catalog.get("catalogSha256"));
        assertEquals(List.of("11", "12"), catalog.get("activeDocumentIds"));
        assertEquals("shipping.md", ((List<Map<String, Object>>) catalog.get("documents"))
                .get(0).get("sourceName"));
        assertEquals("LOGISTICS", ((List<Map<String, Object>>) catalog.get("documents"))
                .get(0).get("domain"));
        assertEquals(7L, ((List<Map<String, Object>>) catalog.get("documents"))
                .get(0).get("chunkCount"));
        assertEquals(1, ((List<Map<String, Object>>) catalog.get("documents"))
                .get(0).get("indexSchemaVersion"));
    }

    @Test
    void rollbackCopiesOldMembershipIntoAHigherRelease() {
        when(jdbcTemplate.queryForObject(
                argThat(sql -> sql.contains("knowledge_release") && sql.contains("FOR UPDATE")),
                eq(Long.class)))
                .thenReturn(9L);
        when(jdbcTemplate.queryForList(
                argThat(sql -> sql != null && sql.contains("SELECT catalog_sha256")),
                eq(4L)))
                .thenReturn(List.of(Map.of("catalog_sha256", "a".repeat(64))));
        when(jdbcTemplate.queryForList(
                argThat(sql -> sql != null && sql.contains("knowledge_release_document")),
                eq(4L)))
                .thenReturn(List.of(Map.of(
                        "document_id", 11L,
                        "source_name", "shipping.md",
                        "content_hash", "b".repeat(64),
                        "version", 1,
                        "domain", "LOGISTICS",
                        "index_schema_version", 1,
                        "chunk_count", 7L)));
        lenient().when(jdbcTemplate.update(
                argThat(sql -> sql != null && sql.contains("SET current_version=?")),
                eq(10L),
                eq(9L)))
                .thenReturn(1);
        when(stringRedisTemplate.opsForValue()).thenReturn(valueOperations);

        Map<String, Object> result = service.activateRelease(
                "rollback-to-v1", "", List.of(), 4L, "admin");

        assertEquals(10L, result.get("releaseVersion"));
        assertEquals(4L, result.get("sourceReleaseVersion"));
        assertEquals(List.of("11"), result.get("activeDocumentIds"));
        verify(jdbcTemplate).update(
                argThat(sql -> sql.contains("INSERT INTO knowledge_release_snapshot")),
                eq(10L),
                eq("rollback-to-v1"),
                eq("a".repeat(64)),
                eq(4L),
                eq("admin"));
    }

    @Test
    void explicitReleaseRejectsCatalogShaThatDoesNotMatchSelectedDocuments() {
        when(jdbcTemplate.queryForObject(
                argThat(sql -> sql.contains("knowledge_release") && sql.contains("FOR UPDATE")),
                eq(Long.class)))
                .thenReturn(9L);
        when(jdbcTemplate.queryForList(
                argThat(sql -> sql != null && sql.contains("d.document_id IN")),
                eq(11L)))
                .thenReturn(List.of(Map.of(
                        "document_id", 11L,
                        "source_name", "shipping.md",
                        "content_hash", "b".repeat(64),
                        "version", 1,
                        "domain", "LOGISTICS",
                        "index_schema_version", 1,
                        "chunk_count", 7L)));

        BusinessException error = assertThrows(BusinessException.class,
                () -> service.activateRelease(
                        "tampered-release", "a".repeat(64), List.of(11L), null, "admin"));

        assertTrue(error.getMessage().contains("所选知识文档集合不一致"));
        verify(jdbcTemplate, never()).batchUpdate(
                argThat(sql -> sql != null && sql.contains("knowledge_release_document")),
                anyList());
        verify(jdbcTemplate, never()).update(
                argThat(sql -> sql != null && sql.contains("INSERT INTO knowledge_release_snapshot")),
                any(), any(), any(), any(), any());
    }

    @Test
    @SuppressWarnings("unchecked")
    void currentSchemaPublishedDocumentSkipsReindex() {
        Map<String, Object> current = document(42L, "PUBLISHED");
        current.put("indexSchemaVersion", 1);
        when(jdbcTemplate.query(
                argThat((String sql) -> sql != null
                        && sql.contains("knowledge_document")
                        && sql.contains("FOR UPDATE")),
                any(RowMapper.class),
                eq(42L)))
                .thenReturn(List.of(current));
        when(jdbcTemplate.queryForObject(
                argThat((String sql) -> sql != null && sql.contains("current_version")),
                eq(Long.class)))
                .thenReturn(9L);

        Map<String, Object> result = service.reindexPublished(42L, "owner");

        assertEquals(false, result.get("reindexed"));
        assertEquals(9L, result.get("releaseVersion"));
        verify(vectorStore, never()).add(anyList());
    }

    @Test
    @SuppressWarnings("unchecked")
    void reindexWritesOriginalContentDomainAndSchemaMetadata() {
        Map<String, Object> legacy = document(42L, "PUBLISHED");
        legacy.put("indexSchemaVersion", 0);
        legacy.put("domain", "LOGISTICS");
        when(jdbcTemplate.query(
                argThat((String sql) -> sql != null
                        && sql.contains("knowledge_document")
                        && sql.contains("FOR UPDATE")),
                any(RowMapper.class),
                eq(42L)))
                .thenReturn(List.of(legacy));
        when(jdbcTemplate.queryForList(
                argThat((String sql) -> sql != null
                        && sql.contains("knowledge_chunk")
                        && sql.contains("status='PUBLISHED'")),
                eq(42L),
                eq(1)))
                .thenReturn(List.of(Map.of(
                        "chunk_id", "knowledge_42_1_0",
                        "chunk_index", 0,
                        "heading", "物流异常",
                        "content", "请从订单详情联系人工客服。",
                        "token_count", 14)));
        lenient().when(jdbcTemplate.update(
                argThat((String sql) -> sql != null
                        && sql.contains("INSERT INTO knowledge_release"))))
                .thenReturn(1);
        when(jdbcTemplate.queryForObject(
                argThat((String sql) -> sql != null
                        && sql.contains("knowledge_release")
                        && sql.contains("FOR UPDATE")),
                eq(Long.class)))
                .thenReturn(5L);
        lenient().when(jdbcTemplate.update(
                argThat((String sql) -> sql != null && sql.contains("SET current_version=?")),
                eq(6L),
                eq(5L)))
                .thenReturn(1);
        when(jdbcTemplate.query(
                argThat((String sql) -> sql != null
                        && sql.contains("FROM knowledge_document WHERE document_id=?")
                        && !sql.contains("FOR UPDATE")),
                any(RowMapper.class),
                eq(42L)))
                .thenReturn(List.of(legacy));

        Map<String, Object> result = service.reindexPublished(42L, "owner");

        assertEquals(true, result.get("reindexed"));
        verify(vectorStore).add(argThat(documents -> {
            Document indexed = documents.get(0);
            Map<String, Object> metadata = indexed.getMetadata();
            return "请从订单详情联系人工客服。".equals(indexed.getText())
                    && "请从订单详情联系人工客服。".equals(metadata.get("originalContent"))
                    && "LOGISTICS".equals(metadata.get("domain"))
                    && "PUBLISHED".equals(metadata.get("status"))
                    && Integer.valueOf(1).equals(metadata.get("indexSchemaVersion"))
                    && Boolean.FALSE.equals(metadata.get("contextEnriched"));
        }));
    }

    @Test
    @SuppressWarnings("unchecked")
    void nonPublishedDocumentCannotBeReindexed() {
        Map<String, Object> draft = document(42L, "DRAFT");
        draft.put("indexSchemaVersion", 0);
        when(jdbcTemplate.query(
                argThat((String sql) -> sql != null
                        && sql.contains("knowledge_document")
                        && sql.contains("FOR UPDATE")),
                any(RowMapper.class),
                eq(42L)))
                .thenReturn(List.of(draft));

        assertThrows(BusinessException.class, () -> service.reindexPublished(42L, "owner"));
        verify(vectorStore, never()).add(anyList());
    }

    @Test
    @SuppressWarnings("unchecked")
    void fullModelRebuildReindexesCurrentPublishedDocuments() {
        Map<String, Object> published = document(42L, "PUBLISHED");
        published.put("domain", "LOGISTICS");
        when(jdbcTemplate.queryForList(
                argThat((String sql) -> sql != null
                        && sql.contains("SELECT document_id")
                        && sql.contains("status='PUBLISHED'")),
                eq(Long.class)))
                .thenReturn(List.of(42L));
        lenient().when(jdbcTemplate.update(
                argThat((String sql) -> sql != null
                        && sql.contains("INSERT INTO knowledge_release"))))
                .thenReturn(1);
        when(jdbcTemplate.queryForObject(
                argThat((String sql) -> sql != null
                        && sql.contains("knowledge_release")
                        && sql.contains("FOR UPDATE")),
                eq(Long.class)))
                .thenReturn(5L);
        when(jdbcTemplate.query(
                argThat((String sql) -> sql != null
                        && sql.contains("knowledge_document")
                        && sql.contains("FOR UPDATE")),
                any(RowMapper.class),
                eq(42L)))
                .thenReturn(List.of(published));
        when(jdbcTemplate.queryForList(
                argThat((String sql) -> sql != null
                        && sql.contains("knowledge_chunk")
                        && sql.contains("status='PUBLISHED'")),
                eq(42L),
                eq(1)))
                .thenReturn(List.of(Map.of(
                        "chunk_id", "knowledge_42_1_0",
                        "chunk_index", 0,
                        "heading", "物流异常",
                        "content", "请从订单详情联系人工客服。",
                        "token_count", 14)));
        lenient().when(jdbcTemplate.update(
                argThat((String sql) -> sql != null
                        && sql.contains("SET current_version=?")),
                eq(6L),
                eq(5L)))
                .thenReturn(1);
        when(stringRedisTemplate.opsForValue()).thenReturn(valueOperations);

        Map<String, Object> result = service.rebuildAllPublishedVectors();

        assertEquals(1, result.get("documents"));
        assertEquals(1, result.get("chunks"));
        assertEquals(6L, result.get("releaseVersion"));
        verify(vectorStore).add(argThat(documents -> {
            Document indexed = documents.get(0);
            return "knowledge_42_1_0".equals(indexed.getId())
                    && "knowledge".equals(indexed.getMetadata().get("dataType"))
                    && "LOGISTICS".equals(indexed.getMetadata().get("domain"));
        }));
    }

    @Test
    void publicationSqlUsesCrossJvmLocksAndCompareAndSet() throws Exception {
        String source = Files.readString(Path.of(
                "src/main/java/com/aishop/biz/impl/KnowledgeBaseServiceImpl.java"));

        assertTrue(source.contains("FROM knowledge_document WHERE document_id=? FOR UPDATE"));
        assertTrue(source.contains("FROM knowledge_release\n                WHERE release_key='global'\n                FOR UPDATE"));
        assertTrue(source.contains("WHERE release_key='global' AND current_version=?"));
        assertTrue(source.contains("opsForValue().set(RELEASE_KEY, String.valueOf(version))"));
        assertTrue(source.contains("transactionalMqSender.sendAfterCommit("));
        assertTrue(source.contains("access_policy"));
        assertTrue(source.contains("UPDATE knowledge_document SET status='DELETED'"));
        assertTrue(source.contains("vectorsRetainedForHistoricalRelease"));
    }

    private static Map<String, Object> document(long documentId, String status) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("documentId", documentId);
        result.put("title", "配送规则");
        result.put("sourceName", "shipping.md");
        result.put("domain", "GENERAL");
        result.put("indexSchemaVersion", 0);
        result.put("status", status);
        result.put("version", 1);
        return result;
    }
}
