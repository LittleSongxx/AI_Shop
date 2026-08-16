package com.aishop.biz.impl;

import com.aishop.biz.KnowledgeBaseService;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.TransactionalMqSender;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.po.RagQuestion;
import com.aishop.entity.query.RagQuestionQuery;
import com.aishop.mappers.RagQuestionMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class RagQuestionOutboxTest {

    @Mock
    private RagQuestionMapper<RagQuestion, RagQuestionQuery> ragQuestionMapper;
    @Mock
    private TransactionalMqSender transactionalMqSender;
    @Mock
    private JdbcTemplate jdbcTemplate;
    @Mock
    private KnowledgeBaseService knowledgeBaseService;
    @InjectMocks
    private RagQuestionServiceImpl service;

    @Test
    void deleteRegistersVectorRemovalInSearchOutbox() {
        when(ragQuestionMapper.deleteByQuestionId(7)).thenReturn(1);

        service.deleteRagQuestionByQuestionId(7);

        verify(transactionalMqSender).sendAfterCommit(
                eq(RabbitMQConfig.RAG_EXCHANGE),
                eq(RabbitMQConfig.RAG_QUEUE_KEY),
                any(),
                org.mockito.ArgumentMatchers.startsWith("rag:faq:7:v:"),
                eq(MessageReliabilityLevelEnum.HIGH));
    }

    @Test
    void saveAndDeleteOperationsAreTransactional() throws Exception {
        assertNotNull(RagQuestionServiceImpl.class
                .getMethod("saveRagQuestion", RagQuestion.class)
                .getAnnotation(Transactional.class));
        assertNotNull(RagQuestionServiceImpl.class
                .getMethod("deleteRagQuestionByQuestionId", Integer.class)
                .getAnnotation(Transactional.class));
    }
}
