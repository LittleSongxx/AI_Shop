package com.aishop.controller.admin;

import com.aishop.entity.po.RagQuestion;
import com.aishop.entity.query.RagQuestionQuery;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.RagQuestionService;
import com.aishop.component.RedisComponent;
import com.aishop.exception.BusinessException;
import com.aishop.utils.AuthCookieHelper;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Date;

@RequestMapping("/admin/rag")
@RestController
public class RagController extends com.aishop.controller.admin.ABaseController {

    @Resource
    private RagQuestionService ragQuestionService;
    @Resource
    private RedisComponent redisComponent;
    @Resource
    private AuthCookieHelper authCookieHelper;

    // 加载RagQuestion
    @PostMapping("/loadRagQuestion")
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
    public ResponseVO saveRagQuestion(RagQuestion question, HttpServletRequest request){
        question.setOwner(currentAdmin(request));
        ragQuestionService.saveRagQuestion(question);
        return getSuccessResponseVO(null);
    }

    // 删除RagQuestion
    @PostMapping("/delRagQuestion")
    public ResponseVO delRagQuestion(Integer questionId){
        ragQuestionService.deleteRagQuestionByQuestionId(questionId);
        return getSuccessResponseVO(null);
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
