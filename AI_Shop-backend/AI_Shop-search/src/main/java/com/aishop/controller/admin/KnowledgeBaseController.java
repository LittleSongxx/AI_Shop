package com.aishop.controller.admin;

import com.aishop.biz.KnowledgeBaseService;
import com.aishop.constants.AdminPermissions;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.exception.BusinessException;
import com.aishop.security.AdminSecurityContext;
import com.aishop.security.RequireAdminPermission;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/admin/knowledge")
public class KnowledgeBaseController extends com.aishop.controller.admin.ABaseController {

    @Resource
    private KnowledgeBaseService knowledgeBaseService;

    @PostMapping("/upload")
    @RequireAdminPermission(AdminPermissions.AI_CONFIG)
    public ResponseVO upload(
            @RequestParam("file") MultipartFile file,
            String title) {
        return getSuccessResponseVO(knowledgeBaseService.upload(
                file, title, currentAdmin()));
    }

    @PostMapping("/publish")
    @RequireAdminPermission(AdminPermissions.AI_CONFIG)
    public ResponseVO publish(Long documentId) {
        if (documentId == null) {
            throw new BusinessException("documentId不能为空");
        }
        return getSuccessResponseVO(knowledgeBaseService.publish(
                documentId, currentAdmin()));
    }

    @PostMapping("/archive")
    @RequireAdminPermission(AdminPermissions.AI_CONFIG)
    public ResponseVO archive(Long documentId) {
        return getSuccessResponseVO(knowledgeBaseService.archive(documentId));
    }

    @PostMapping("/documents")
    @RequireAdminPermission(value = {AdminPermissions.AI_CONFIG, AdminPermissions.AUDIT_READ}, requireAll = false)
    public ResponseVO documents(Integer pageNo, Integer pageSize, String status) {
        return getSuccessResponseVO(knowledgeBaseService.listDocuments(
                pageNo == null ? 1 : pageNo,
                pageSize == null ? 30 : pageSize,
                status));
    }

    @PostMapping("/jobs")
    @RequireAdminPermission(value = {AdminPermissions.AI_CONFIG, AdminPermissions.AUDIT_READ}, requireAll = false)
    public ResponseVO jobs(Integer pageNo, Integer pageSize, String status) {
        return getSuccessResponseVO(knowledgeBaseService.listJobs(
                pageNo == null ? 1 : pageNo,
                pageSize == null ? 30 : pageSize,
                status));
    }

    @PostMapping("/faqCandidates")
    @RequireAdminPermission(value = {AdminPermissions.AI_CONFIG, AdminPermissions.AI_EVALUATE}, requireAll = false)
    public ResponseVO faqCandidates(Integer pageNo, Integer pageSize, String status) {
        return getSuccessResponseVO(knowledgeBaseService.listFaqCandidates(
                pageNo == null ? 1 : pageNo,
                pageSize == null ? 30 : pageSize,
                status));
    }

    @PostMapping("/reviewFaqCandidate")
    @RequireAdminPermission(AdminPermissions.AI_CONFIG)
    public ResponseVO reviewFaqCandidate(
            Long candidateId,
            Boolean approved,
            String remark,
            String correctedAnswer,
            String category) {
        if (candidateId == null) {
            throw new BusinessException("candidateId不能为空");
        }
        return getSuccessResponseVO(knowledgeBaseService.reviewFaqCandidate(
                candidateId,
                Boolean.TRUE.equals(approved),
                currentAdmin(),
                remark,
                correctedAnswer,
                category));
    }

    private String currentAdmin() {
        return AdminSecurityContext.requirePrincipal().getAccount();
    }
}
