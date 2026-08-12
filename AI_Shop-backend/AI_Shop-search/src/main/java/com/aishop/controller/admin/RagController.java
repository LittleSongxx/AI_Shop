package com.aishop.controller.admin;

import com.aishop.entity.po.RagQuestion;
import com.aishop.entity.query.RagQuestionQuery;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.RagQuestionService;
import com.aishop.constants.AdminPermissions;
import com.aishop.security.AdminSecurityContext;
import com.aishop.security.RequireAdminPermission;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Date;

@RequestMapping("/admin/rag")
@RestController
public class RagController extends com.aishop.controller.admin.ABaseController {

    @Resource
    private RagQuestionService ragQuestionService;

    // 加载RagQuestion
    @PostMapping("/loadRagQuestion")
    @RequireAdminPermission(value = {AdminPermissions.AI_CONFIG, AdminPermissions.AUDIT_READ}, requireAll = false)
    public ResponseVO loadRagQuestion(
            Integer pageNo,
            Integer pageSize,
            String questionFuzzy,
            String category,
            String publishStatus){
        RagQuestionQuery query = new RagQuestionQuery();
        query.setPageNo(pageNo);
        query.setPageSize(pageSize);
        query.setQuestionFuzzy(questionFuzzy);
        query.setCategory(category);
        query.setPublishStatus(publishStatus);
        PaginationResultVO<RagQuestion> resultVO = ragQuestionService.findListByPage(query);
        return getSuccessResponseVO(resultVO);
    }

    // 保存RagQuestion
    @PostMapping("/saveRagQuestion")
    @RequireAdminPermission(AdminPermissions.AI_CONFIG)
    public ResponseVO saveRagQuestion(RagQuestion question){
        question.setOwner(currentAdmin());
        ragQuestionService.saveRagQuestion(question);
        return getSuccessResponseVO(null);
    }

    // 删除RagQuestion
    @PostMapping("/delRagQuestion")
    @RequireAdminPermission(AdminPermissions.AI_CONFIG)
    public ResponseVO delRagQuestion(Integer questionId){
        ragQuestionService.deleteRagQuestionByQuestionId(questionId);
        return getSuccessResponseVO(null);
    }

    private String currentAdmin() {
        return AdminSecurityContext.requirePrincipal().getAccount();
    }
}
