package com.aishop.controller.internal;

import com.aishop.biz.KnowledgeBaseService;
import com.aishop.controller.ABaseController;
import com.aishop.entity.vo.ResponseVO;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;
import java.util.List;

@RestController
@RequestMapping("/internal/search/knowledge")
public class KnowledgeInternalController extends ABaseController {

    @Resource
    private KnowledgeBaseService knowledgeBaseService;

    @PostMapping("/version")
    public ResponseVO<Long> version() {
        return getSuccessResponseVO(knowledgeBaseService.releaseVersion());
    }

    @PostMapping("/topFaq")
    public ResponseVO<List<Map<String, Object>>> topFaq(
            @RequestBody(required = false) Map<String, Object> body) {
        int limit = body != null && body.get("limit") instanceof Number number
                ? number.intValue() : 100;
        return getSuccessResponseVO(knowledgeBaseService.topFaq(limit));
    }

    @PostMapping("/faqExact")
    public ResponseVO<Map<String, Object>> faqExact(
            @RequestBody Map<String, Object> body) {
        return getSuccessResponseVO(knowledgeBaseService.exactFaq(
                text(body.get("question")),
                text(body.get("language")),
                text(body.get("channel"))));
    }

    @PostMapping("/faqCandidate")
    public ResponseVO<Void> faqCandidate(@RequestBody Map<String, Object> body) {
        Integer messageId = body.get("sourceMessageId") instanceof Number number
                ? number.intValue() : null;
        knowledgeBaseService.submitFaqCandidate(
                text(body.get("question")),
                text(body.get("answer")),
                messageId,
                text(body.get("category")));
        return getSuccessResponseVO(null);
    }

    private String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }
}
