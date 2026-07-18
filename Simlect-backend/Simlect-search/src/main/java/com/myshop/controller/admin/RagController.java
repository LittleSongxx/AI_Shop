package com.myshop.controller.admin;

import com.myshop.entity.po.RagQuestion;
import com.myshop.entity.query.RagQuestionQuery;
import com.myshop.entity.vo.PaginationResultVO;
import com.myshop.entity.vo.ResponseVO;
import com.myshop.biz.RagQuestionService;
import jakarta.annotation.Resource;
import jakarta.validation.constraints.NotEmpty;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Date;

@RequestMapping("/admin/rag")
@RestController
public class RagController extends com.myshop.controller.admin.ABaseController {

    @Resource
    private RagQuestionService ragQuestionService;

    // 加载RagQuestion
    @PostMapping("/loadRagQuestion")
    public ResponseVO loadRagQuestion(Integer pageNo, Integer pageSize, String questionFuzzy){
        RagQuestionQuery query = new RagQuestionQuery();
        query.setPageNo(pageNo);
        query.setPageSize(pageSize);
        query.setQuestionFuzzy(questionFuzzy);
        PaginationResultVO<RagQuestion> resultVO = ragQuestionService.findListByPage(query);
        return getSuccessResponseVO(resultVO);
    }

    // 保存RagQuestion
    @PostMapping("/saveRagQuestion")
    public ResponseVO saveRagQuestion(Integer questionId, String similarQuestion, @NotEmpty String question, @NotEmpty String answer){
        ragQuestionService.saveRagQuestion(questionId,similarQuestion,question,answer);
        return getSuccessResponseVO(null);
    }

    // 删除RagQuestion
    @PostMapping("/delRagQuestion")
    public ResponseVO delRagQuestion(Integer questionId){
        ragQuestionService.deleteRagQuestionByQuestionId(questionId);
        return getSuccessResponseVO(null);
    }
}
