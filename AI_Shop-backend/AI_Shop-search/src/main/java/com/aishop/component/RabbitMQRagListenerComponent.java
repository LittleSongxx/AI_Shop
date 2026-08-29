package com.aishop.component;

import com.aishop.api.support.ProductFeignSupport;
import com.aishop.api.vo.ProductRagIndexVO;
import com.aishop.api.vo.ProductRagPropertyVO;
import com.aishop.api.vo.ProductRagSkuVO;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.entity.dto.RagDataDTO;
import com.aishop.api.enums.ProductStatusEnum;
import com.aishop.entity.enums.RagDataTypeEnum;
import com.aishop.entity.po.RagQuestion;
import com.aishop.exception.BusinessException;
import com.aishop.biz.RagQuestionService;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.rabbitmq.client.Channel;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.elasticsearch.client.Request;
import org.elasticsearch.client.RestClient;
import org.springframework.ai.document.Document;
import org.springframework.ai.vectorstore.VectorStore;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

@Component
@Slf4j
public class RabbitMQRagListenerComponent {

    @Value("${spring.ai.vectorstore.elasticsearch.index-name}")
    private String indexName;

    @Resource
    private VectorStore vectorStore;
    @Resource
    private RagQuestionService ragQuestionService;
    @Resource
    private ProductFeignSupport productFeignSupport;
    @Resource
    private RestClient restClient;
    @Resource
    private ObjectMapper objectMapper;
    @Resource
    private MqListenerHelper mqListenerHelper;
    @Resource
    private MqConsumeFailureRecorder mqConsumeFailureRecorder;

    @RabbitListener(queues = RabbitMQConfig.RAG_QUEUE, ackMode = "MANUAL")
    public void handleRagData(RagDataDTO ragDataDTO, Channel channel, Message message) throws IOException {
        long deliveryTag = message.getMessageProperties().getDeliveryTag();
        if (!mqListenerHelper.tryBeginConsume(message, MqListenerHelper.CONSUME_IDEMPOTENCY_TTL_HIGH_SECONDS)) {
            mqListenerHelper.ackCompletedOrDeferBusy(
                    channel, deliveryTag, message, RabbitMQConfig.RAG_QUEUE);
            return;
        }
        if (ragDataDTO == null) {
            log.error("RAG同步失败，数据为空");
            mqConsumeFailureRecorder.record(RabbitMQConfig.RAG_QUEUE, message, null,
                    new BusinessException("RAG 消息体为空"));
            mqListenerHelper.releaseConsume(message);
            channel.basicNack(deliveryTag, false, false);
            return;
        }
        try {
            if (ragDataDTO.getDataId() == null) {
                log.warn("获取到的dataId为空，跳过处理");
                mqConsumeFailureRecorder.record(RabbitMQConfig.RAG_QUEUE, message, ragDataDTO,
                        new BusinessException("RAG dataId 为空"));
                mqListenerHelper.releaseConsume(message);
                channel.basicNack(deliveryTag, false, false);
                return;
            }
            RagDataTypeEnum ragDataTypeEnum = RagDataTypeEnum.getByType(ragDataDTO.getType());
            if (ragDataTypeEnum == null) {
                log.error("未知的数据类型: {}", ragDataDTO.getType());
                mqConsumeFailureRecorder.record(RabbitMQConfig.RAG_QUEUE, message, ragDataDTO,
                        new BusinessException("未知 RAG 类型"));
                mqListenerHelper.releaseConsume(message);
                channel.basicNack(deliveryTag, false, false);
                return;
            }
            switch (ragDataTypeEnum) {
                case PRODUCT:
                    save2vectorDB4Product(ragDataDTO.getDataId());
                    mqListenerHelper.clearConsumeRetry(RabbitMQConfig.RAG_QUEUE, message);
                    channel.basicAck(deliveryTag, false);
                    return;
                case FAQ:
                    save2vectorDB4FAQ(ragDataDTO.getDataId());
                    mqListenerHelper.clearConsumeRetry(RabbitMQConfig.RAG_QUEUE, message);
                    channel.basicAck(deliveryTag, false);
            }
        } catch (Exception e) {
            log.error("RAG同步失败", e);
            mqListenerHelper.nackWithRetryOrDlq(channel, deliveryTag, message,
                    RabbitMQConfig.RAG_QUEUE, ragDataDTO, e);
        }
    }
    private void save2vectorDB4FAQ(String dataId) {
        String documentId = RagDataTypeEnum.FAQ.getType() + dataId;
        // 获取RagQuestion
        RagQuestion ragQuestion = null;
        try {
            ragQuestion = ragQuestionService.getRagQuestionByQuestionId(Integer.parseInt(dataId));
        } catch (NumberFormatException e) {
            log.error("FAQ dataId格式错误: {}", dataId, e);
            throw new BusinessException("FAQ dataId格式错误: " + dataId, e);  // 抛到外层 → NACK → DLQ
        }
        // 判断是否存在
        Date now = new Date();
        boolean unavailable = ragQuestion == null
                || !"PUBLISHED".equalsIgnoreCase(ragQuestion.getPublishStatus())
                || (ragQuestion.getEffectiveStart() != null
                    && ragQuestion.getEffectiveStart().after(now))
                || (ragQuestion.getEffectiveEnd() != null
                    && !ragQuestion.getEffectiveEnd().after(now));
        if (unavailable){
            vectorStore.delete(List.of(documentId));
            return;
        }
        List<Document> documentList = new ArrayList<>();
        Map<String, Object> metaData = new HashMap<>();
        // 先删除旧数据，再新增（不存在也不会报错）
        vectorStore.delete(List.of(documentId));
        metaData.put("questionId", dataId);
        metaData.put("dataType",RagDataTypeEnum.FAQ.getType());
        metaData.put("question", ragQuestion.getQuestion());
        metaData.put("answer", ragQuestion.getAnswer());
        metaData.put("category", ragQuestion.getCategory());
        metaData.put("language", ragQuestion.getLanguage());
        metaData.put("channel", ragQuestion.getChannel());
        metaData.put("priority", ragQuestion.getPriority());
        metaData.put("version", ragQuestion.getVersion());
        metaData.put("source", ragQuestion.getSource());
        metaData.put("owner", ragQuestion.getOwner());
        // FAQ remains public in the single-store boundary; knowledge documents
        // may carry an explicit accessPolicy.
        metaData.put("accessPolicy", "PUBLIC");
        // 时效性字段（epoch ms），供检索侧过滤过期 FAQ，与精确 SQL 路径行为保持一致
        if (ragQuestion.getEffectiveStart() != null) {
            metaData.put("effectiveStart", ragQuestion.getEffectiveStart().getTime());
        }
        if (ragQuestion.getEffectiveEnd() != null) {
            metaData.put("effectiveEnd", ragQuestion.getEffectiveEnd().getTime());
        }
        // 如果有相似问题则填充
        if(!StringUtils.isEmpty(ragQuestion.getSimilarQuestion())){
            metaData.put("similarQuestion", ragQuestion.getSimilarQuestion());
        }else {
            metaData.put("similarQuestion", "暂无相似问题");
        }
        StringBuilder content = new StringBuilder();
        content.append(ragQuestion.getQuestion()).append("\n\n");
        // 如果有相似问题则填充
        if(!StringUtils.isEmpty(ragQuestion.getSimilarQuestion())){
            content.append("相似问题：").append(ragQuestion.getSimilarQuestion()).append("\n\n");
        }
        content.append("答案：").append(ragQuestion.getAnswer());
        Document document = new Document(RagDataTypeEnum.FAQ.getType() + dataId, content.toString(), metaData);
        documentList.add(document);
        // 过滤掉无效文档
        List<Document> validDocuments = documentList.stream()
                .filter(doc -> doc != null)
                .filter(doc -> doc.getText() != null)
                .filter(doc -> !doc.getText().trim().isEmpty())
                .collect(Collectors.toList());
        log.info("添加FAQ文档，有效数量: {} 条", validDocuments.size());
        vectorStore.add(validDocuments);
    }

    private void save2vectorDB4Product(String productId) {
        String prefixId = RagDataTypeEnum.PRODUCT.getType() + productId;  // ← 新增，统一开头声明
        ProductRagIndexVO productInfo = productFeignSupport.getRagIndex(productId);
        if (productInfo == null) {
            // 商品不存在 → 全部删掉
            deleteByProductId(productId);
            return;
        }
        // 先收集所有要写入的文档 ID
        List<String> newDocIds = new ArrayList<>();
        // 商品名 商品属性名：商品属性值
        List<Document> documentList = new ArrayList<>();
        // 判断商品是否在售
        if (ProductStatusEnum.ON_SALE.getStatus().equals(productInfo.getStatus())) {
            // 基础信息切片
            String baseId = prefixId;
            newDocIds.add(baseId);
            StringBuilder context = new StringBuilder();
            context.append("商品：").append(productInfo.getProductName()).append("\n");
            appendLine(context, "品牌", productInfo.getBrand());
            appendLine(context, "类目", productInfo.getCategoryId());
            appendLine(context, "父类目", productInfo.getParentCategoryId());
            appendLine(context, "描述", productInfo.getProductDesc());
            Map<String, Object> metaData = new HashMap<>();
            metaData.put("productId", productId);
            metaData.put("dataType", RagDataTypeEnum.PRODUCT.getType());
            metaData.put("chunkType", "base");
            metaData.put("productName", productInfo.getProductName());
            putIfNotBlank(metaData, "brand", productInfo.getBrand());
            putIfNotBlank(metaData, "categoryId", productInfo.getCategoryId());
            putIfNotBlank(metaData, "parentCategoryId", productInfo.getParentCategoryId());
            Document document = new Document(prefixId, context.toString(), metaData);
            documentList.add(document);
            log.info("添加基础商品文档 - ID: {}, content: {}, metaData: {}",
                    document.getId(), document.getText(), document.getMetadata());
            List<ProductRagPropertyVO> dbPropertyList = productInfo.getPropertyValues() == null
                    ? List.of() : productInfo.getPropertyValues();
            Map<String, ProductRagPropertyVO> propertyMap = dbPropertyList.stream().collect(Collectors.toMap(ProductRagPropertyVO::getPropertyValueId,
                    Function.identity(), (data1, data2) -> data2));
            if (!dbPropertyList.isEmpty()) {
                String attributesId = prefixId + "_attributes";
                StringBuilder attributes = new StringBuilder();
                attributes.append("商品：").append(productInfo.getProductName()).append("\n属性与卖点：\n");
                for (ProductRagPropertyVO property : dbPropertyList) {
                    attributes.append(property.getPropertyName()).append("：")
                            .append(property.getPropertyValue()).append("\n");
                }
                Map<String, Object> attributesMeta = new HashMap<>(metaData);
                attributesMeta.put("chunkType", "attributes");
                documentList.add(new Document(attributesId, attributes.toString(), attributesMeta));
                newDocIds.add(attributesId);
            }
            if (StringUtils.isNotBlank(productInfo.getProductDesc())) {
                String scenarioId = prefixId + "_scenarios";
                Map<String, Object> scenarioMeta = new HashMap<>(metaData);
                scenarioMeta.put("chunkType", "scenarios");
                documentList.add(new Document(
                        scenarioId,
                        "商品：" + productInfo.getProductName()
                                + "\n使用场景与适用人群：" + productInfo.getProductDesc(),
                        scenarioMeta));
                newDocIds.add(scenarioId);
            }
            List<ProductRagSkuVO> dbSkuList = productInfo.getSkus() == null
                    ? List.of() : productInfo.getSkus();
            // 存带SKU的商品属性
            for (ProductRagSkuVO dbSku : dbSkuList) {
                // 库存已迁至 aishop_stock；RAG 索引不再按 SKU.stock 过滤
                String propertyValueIdsStr = dbSku.getPropertyValueIds();
                if (propertyValueIdsStr == null) {
                    log.warn("SKU属性值ID为空");
                    continue;
                }
                String[] propertyValueIds = propertyValueIdsStr.split("-");
                Map<String, Object> metaDataWithSKU = new HashMap<>();
                Map<String, Object> skuMap = new HashMap<>();
                // 多个属性拼接成一个字符串
                int index = 0;
                StringBuilder contextWithSKU = new StringBuilder();
                for (String propertyValueId : propertyValueIds) {
                    index++;
                    ProductRagPropertyVO tempPropertyValue = propertyMap.get(propertyValueId);
                    if (tempPropertyValue == null) {
                        log.warn("属性值不存在, productId={}, propertyValueId={}", productId, propertyValueId);
                        continue;
                    }
                    contextWithSKU.append(productInfo.getProductName()).append(" ")
                            .append(tempPropertyValue.getPropertyName()).append(": ").append(tempPropertyValue.getPropertyValue());
                    // 如果不是最后一个则.append(",")
                    if (index < propertyValueIds.length) {
                        contextWithSKU.append(",");
                    }
                }
                metaDataWithSKU.put("productId", productId);
                metaDataWithSKU.put("dataType", RagDataTypeEnum.PRODUCT.getType());
                metaDataWithSKU.put("chunkType", "sku");
                metaDataWithSKU.put("productName", productInfo.getProductName());
                putIfNotBlank(metaDataWithSKU, "brand", productInfo.getBrand());
                putIfNotBlank(metaDataWithSKU, "categoryId", productInfo.getCategoryId());
                skuMap.put("skuProperty", contextWithSKU);
                metaDataWithSKU.put("skuList", skuMap);
                Document documentWithSKU = new Document(prefixId + "_" + dbSku.getPropertyValueIdHash(), contextWithSKU.toString(), metaDataWithSKU);
                newDocIds.add(prefixId + "_" + dbSku.getPropertyValueIdHash());
                documentList.add(documentWithSKU);
                log.info("添加SKU文档 - ID: {}, content: {}, metaData: {}",
                        documentWithSKU.getId(), documentWithSKU.getText(), documentWithSKU.getMetadata());
                // 大模型限制一次最多10条
                if (documentList.size() >= 10) {
                    log.info("批量添加到向量数据库: {} 条", documentList.size());
                    // 打印每个文档信息以便调试
                    for (int i = 0; i < documentList.size(); i++) {
                        Document doc = documentList.get(i);
                        log.info("文档 {}: id={}, content.length={}",
                                i,
                                doc.getId(),
                                doc.getText() != null ? doc.getText().length() : "null");
                    }
                    // 过滤掉无效文档
                    List<Document> validDocuments = filterValidDocuments(documentList);
                    log.info("有效文档数量: {} 条", validDocuments.size());
                    vectorStore.add(validDocuments);
                    // 清空List
                    documentList.clear();
                }
         }
        }
        // 添加剩余的文档
        if (!documentList.isEmpty()){
            log.info("添加剩余文档到向量数据库: {} 条", documentList.size());
            // 打印每个文档信息以便调试
            for (int i = 0; i < documentList.size(); i++) {
                Document doc = documentList.get(i);
                log.info("文档 {}: id={}, content.length={}",
                        i,
                        doc.getId(),
                        doc.getText() != null ? doc.getText().length() : "null");
            }
            // 过滤掉无效文档
            List<Document> validDocuments = filterValidDocuments(documentList);
            log.info("有效文档数量: {} 条", validDocuments.size());
            vectorStore.add(validDocuments);
        }
        // 全部写成功后才删旧数据
        deleteStaleDocuments(productId, newDocIds);
    }
    private void deleteStaleDocuments(String productId, List<String> keepIds) {
        if (keepIds == null || keepIds.isEmpty()) {
            deleteByProductId(productId);
            return;
        }
        try {
            String endpoint = "/" + indexName + "/_delete_by_query";
            Request request = new Request("POST", endpoint);
            request.setJsonEntity(objectMapper.writeValueAsString(Map.of(
                    "query", Map.of(
                            "bool", Map.of(
                                    "must", productIdentityFilters(productId),
                                    "must_not", List.of(Map.of(
                                            "ids", Map.of("values", keepIds))))))));
            restClient.performRequest(request);
            log.info("已清理商品 {} 的旧文档, 保留 {} 条", productId, keepIds.size());
        } catch (IOException e) {
            log.error("清理旧文档失败, productId: {}", productId, e);
            throw new BusinessException("清理商品旧向量文档失败", e);
        }
    }

    private void deleteByProductId(String productId) {
        try {
            String endpoint = "/" + indexName + "/_delete_by_query";
            Request request = new Request("POST", endpoint);
            request.setJsonEntity(objectMapper.writeValueAsString(Map.of(
                    "query", Map.of(
                            "bool", Map.of(
                                    "must", productIdentityFilters(productId))))));
            restClient.performRequest(request);
            log.info("已删除商品 {} 的所有向量文档", productId);
        } catch (IOException e) {
            log.error("删除商品向量文档失败, productId: {}", productId, e);
            throw new BusinessException("删除商品向量文档失败", e);
        }
    }

    private List<Map<String, Object>> productIdentityFilters(String productId) {
        return List.of(
                Map.of("term", Map.of("metadata.productId", productId)),
                Map.of(
                        "term",
                        Map.of("metadata.dataType", RagDataTypeEnum.PRODUCT.getType())));
    }

    private List<Document> filterValidDocuments(List<Document> documents) {
        return documents.stream()
                .filter(doc -> doc != null && doc.getText() != null && !doc.getText().trim().isEmpty())
                .collect(Collectors.toList());
    }

    private void appendLine(StringBuilder builder, String label, String value) {
        if (StringUtils.isNotBlank(value)) {
            builder.append(label).append("：").append(value).append("\n");
        }
    }

    private void putIfNotBlank(Map<String, Object> metadata, String key, String value) {
        if (StringUtils.isNotBlank(value)) {
            metadata.put(key, value);
        }
    }
}
