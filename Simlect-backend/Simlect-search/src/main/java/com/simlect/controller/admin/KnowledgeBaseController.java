package com.simlect.controller.admin;

import com.simlect.biz.KnowledgeBaseService;
import com.simlect.component.RedisComponent;
import com.simlect.entity.vo.ResponseVO;
import com.simlect.exception.BusinessException;
import com.simlect.utils.AuthCookieHelper;
import com.simlect.utils.StringTools;
import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/admin/knowledge")
public class KnowledgeBaseController extends com.simlect.controller.admin.ABaseController {

    @Resource
    private KnowledgeBaseService knowledgeBaseService;
    @Resource
    private RedisComponent redisComponent;
    @Resource
    private AuthCookieHelper authCookieHelper;

    @PostMapping("/upload")
    public ResponseVO upload(
            @RequestParam("file") MultipartFile file,
            String title,
            HttpServletRequest request) {
        return getSuccessResponseVO(knowledgeBaseService.upload(
                file, title, currentAdmin(request)));
    }

    @PostMapping("/publish")
    public ResponseVO publish(Long documentId, HttpServletRequest request) {
        if (documentId == null) {
            throw new BusinessException("documentId不能为空");
        }
        return getSuccessResponseVO(knowledgeBaseService.publish(
                documentId, currentAdmin(request)));
    }

    @PostMapping("/archive")
    public ResponseVO archive(Long documentId) {
        return getSuccessResponseVO(knowledgeBaseService.archive(documentId));
    }

    @PostMapping("/documents")
    public ResponseVO documents(Integer pageNo, Integer pageSize, String status) {
        return getSuccessResponseVO(knowledgeBaseService.listDocuments(
                pageNo == null ? 1 : pageNo,
                pageSize == null ? 30 : pageSize,
                status));
    }

    @PostMapping("/jobs")
    public ResponseVO jobs(Integer pageNo, Integer pageSize, String status) {
        return getSuccessResponseVO(knowledgeBaseService.listJobs(
                pageNo == null ? 1 : pageNo,
                pageSize == null ? 30 : pageSize,
                status));
    }

    @PostMapping("/faqCandidates")
    public ResponseVO faqCandidates(Integer pageNo, Integer pageSize, String status) {
        return getSuccessResponseVO(knowledgeBaseService.listFaqCandidates(
                pageNo == null ? 1 : pageNo,
                pageSize == null ? 30 : pageSize,
                status));
    }

    @PostMapping("/reviewFaqCandidate")
    public ResponseVO reviewFaqCandidate(
            Long candidateId,
            Boolean approved,
            String remark,
            String correctedAnswer,
            String category,
            HttpServletRequest request) {
        if (candidateId == null) {
            throw new BusinessException("candidateId不能为空");
        }
        return getSuccessResponseVO(knowledgeBaseService.reviewFaqCandidate(
                candidateId,
                Boolean.TRUE.equals(approved),
                currentAdmin(request),
                remark,
                correctedAnswer,
                category));
    }

    private String currentAdmin(HttpServletRequest request) {
        String token = authCookieHelper.resolveAdminToken(request);
        Object account = redisComponent.getLoginInfo4Admin(token);
        if (account == null || StringTools.isEmpty(String.valueOf(account))) {
            throw new BusinessException("管理员登录已失效");
        }
        return String.valueOf(account);
    }
}
