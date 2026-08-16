package com.aishop.controller.internal;

import com.aishop.api.support.ProductFeignSupport;
import com.aishop.biz.RagQuestionService;
import com.aishop.component.EsSearchComponent;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.ReliableMessageSender;
import com.aishop.controller.ABaseController;
import com.aishop.entity.dto.RagDataDTO;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.enums.RagDataTypeEnum;
import com.aishop.entity.po.RagQuestion;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.support.MqIdempotencyKeys;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RequestMapping("/internal/search/tool")
@RestController
@Slf4j
public class SearchToolInternalController extends ABaseController {

    @Resource
    private EsSearchComponent esSearchComponent;
    @Resource
    private ProductFeignSupport productFeignSupport;
    @Resource
    private RagQuestionService ragQuestionService;
    @Resource
    private ReliableMessageSender reliableMessageSender;

    @PostMapping("/productData")
    public ResponseVO<Void> productData(
            @RequestParam(name = "includeVector", defaultValue = "true") boolean includeVector,
            @RequestParam(name = "includeKeyword", defaultValue = "true") boolean includeKeyword) {
        List<String> productIds = productFeignSupport.listOnSaleProductIds();
        for (String productId : productIds) {
            if (includeKeyword) {
                esSearchComponent.saveIndex(productId);
            }
            if (includeVector) {
                RagDataDTO ragDataDTO = new RagDataDTO(productId, RagDataTypeEnum.PRODUCT.getType());
                reliableMessageSender.sendMessage(
                        RabbitMQConfig.RAG_EXCHANGE,
                        RabbitMQConfig.RAG_QUEUE_KEY,
                        ragDataDTO,
                        MqIdempotencyKeys.ragProduct(productId, ragDataDTO.getVersion()),
                        MessageReliabilityLevelEnum.HIGH);
            }
        }
        log.info(
                "product_index_rebuild_completed count={} includeKeyword={} includeVector={}",
                productIds.size(),
                includeKeyword,
                includeVector);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/ragData")
    public ResponseVO<Void> ragData() {
        List<RagQuestion> list = ragQuestionService.findListByParam(null);
        if (list != null) {
            for (RagQuestion ragQuestion : list) {
                RagDataDTO ragDataDTO = new RagDataDTO(
                        ragQuestion.getQuestionId().toString(), RagDataTypeEnum.FAQ.getType());
                reliableMessageSender.sendMessage(
                        RabbitMQConfig.RAG_EXCHANGE,
                        RabbitMQConfig.RAG_QUEUE_KEY,
                        ragDataDTO,
                        MqIdempotencyKeys.ragFaq(
                                String.valueOf(ragQuestion.getQuestionId()), ragDataDTO.getVersion()),
                        MessageReliabilityLevelEnum.HIGH);
            }
        }
        return getSuccessResponseVO(null);
    }
}
