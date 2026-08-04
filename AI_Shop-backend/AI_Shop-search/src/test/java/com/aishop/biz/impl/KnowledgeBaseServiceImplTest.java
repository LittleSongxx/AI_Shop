package com.aishop.biz.impl;

import com.aishop.component.ContextPrefixEnricher;
import com.aishop.exception.BusinessException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.ai.vectorstore.VectorStore;
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
import static org.mockito.Mockito.doThrow;
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
    void archiveCommitIsNotReportedAsFailureWhenVectorCleanupFails() {
        when(stringRedisTemplate.opsForValue()).thenReturn(valueOperations);
        when(jdbcTemplate.query(
                argThat(sql -> sql.contains("knowledge_document") && sql.contains("FOR UPDATE")),
                any(RowMapper.class),
                eq(42L)))
                .thenReturn(List.of(document(42L, "PUBLISHED")));
        when(jdbcTemplate.queryForList(
                argThat(sql -> sql.contains("knowledge_chunk")),
                eq(String.class),
                eq(42L)))
                .thenReturn(List.of("knowledge_42_1_0"));
        when(jdbcTemplate.queryForObject(
                argThat(sql -> sql.contains("knowledge_release") && sql.contains("FOR UPDATE")),
                eq(Long.class)))
                .thenReturn(5L);
        lenient().when(jdbcTemplate.update(
                argThat(sql -> sql.contains("SET current_version=?")),
                eq(6L),
                eq(5L)))
                .thenReturn(1);
        doThrow(new IllegalStateException("ES unavailable"))
                .when(vectorStore).delete(List.of("knowledge_42_1_0"));

        Map<String, Object> result = service.archive(42L);

        assertEquals("ARCHIVED", result.get("status"));
        assertEquals(6L, result.get("releaseVersion"));
        verify(jdbcTemplate).update(
                argThat(sql -> sql.contains("status='FAILED'")
                        && sql.contains("stage='ARCHIVE_CLEANUP'")),
                eq("ES unavailable"),
                eq(0L));
    }

    @Test
    void catalogContainsOnlyPublishedDocumentIdsFromTheCatalogQuery() {
        when(jdbcTemplate.queryForObject(
                argThat(sql -> sql.contains("current_version")),
                eq(Long.class)))
                .thenReturn(9L);
        when(jdbcTemplate.queryForList(
                argThat(sql -> sql.contains("status='PUBLISHED'")),
                eq(Long.class)))
                .thenReturn(List.of(11L, 12L));

        Map<String, Object> catalog = service.releaseCatalog();

        assertEquals(9L, catalog.get("version"));
        assertEquals(List.of("11", "12"), catalog.get("activeDocumentIds"));
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
    }

    private static Map<String, Object> document(long documentId, String status) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("documentId", documentId);
        result.put("title", "配送规则");
        result.put("sourceName", "shipping.md");
        result.put("status", status);
        result.put("version", 1);
        return result;
    }
}
