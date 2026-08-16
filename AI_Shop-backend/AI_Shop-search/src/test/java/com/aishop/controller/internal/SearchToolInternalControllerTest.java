package com.aishop.controller.internal;

import com.aishop.api.support.ProductFeignSupport;
import com.aishop.biz.RagQuestionService;
import com.aishop.component.EsSearchComponent;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.ReliableMessageSender;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.vo.ResponseVO;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class SearchToolInternalControllerTest {

    @Mock
    private EsSearchComponent esSearchComponent;
    @Mock
    private ProductFeignSupport productFeignSupport;
    @Mock
    private RagQuestionService ragQuestionService;
    @Mock
    private ReliableMessageSender reliableMessageSender;

    @InjectMocks
    private SearchToolInternalController controller;

    @Test
    void keywordOnlyRebuildDoesNotPublishEmbeddingTasks() {
        when(productFeignSupport.listOnSaleProductIds()).thenReturn(List.of("P000000000000001", "P000000000000002"));

        ResponseVO<Void> response = controller.productData(false, true);

        assertNotNull(response);
        verify(esSearchComponent).saveIndex("P000000000000001");
        verify(esSearchComponent).saveIndex("P000000000000002");
        verify(reliableMessageSender, never()).sendMessage(
                anyString(), anyString(), any(), anyString(), any(MessageReliabilityLevelEnum.class));
    }

    @Test
    void defaultRebuildStillPublishesEmbeddingTasks() {
        when(productFeignSupport.listOnSaleProductIds()).thenReturn(List.of("P000000000000001"));

        controller.productData(true, true);

        verify(reliableMessageSender).sendMessage(
                eq(RabbitMQConfig.RAG_EXCHANGE),
                eq(RabbitMQConfig.RAG_QUEUE_KEY),
                any(),
                anyString(),
                eq(MessageReliabilityLevelEnum.HIGH));
    }

    @Test
    void vectorOnlyRebuildDoesNotRewriteKeywordIndex() {
        when(productFeignSupport.listOnSaleProductIds())
                .thenReturn(List.of("P000000000000001"));

        controller.productData(true, false);

        verify(esSearchComponent, never()).saveIndex(anyString());
        verify(reliableMessageSender).sendMessage(
                eq(RabbitMQConfig.RAG_EXCHANGE),
                eq(RabbitMQConfig.RAG_QUEUE_KEY),
                any(),
                anyString(),
                eq(MessageReliabilityLevelEnum.HIGH));
    }
}
