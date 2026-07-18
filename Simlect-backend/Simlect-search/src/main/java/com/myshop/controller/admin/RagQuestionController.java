package com.myshop.controller.admin;

import java.util.List;

import com.myshop.entity.query.RagQuestionQuery;
import com.myshop.entity.po.RagQuestion;
import com.myshop.entity.vo.ResponseVO;
import com.myshop.biz.RagQuestionService;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("ragQuestionController")
@RequestMapping("/admin/ragQuestion")
public class RagQuestionController extends com.myshop.controller.admin.ABaseController{

	@Resource
	private RagQuestionService ragQuestionService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(RagQuestionQuery query){
		return getSuccessResponseVO(ragQuestionService.findListByPage(query));
	}

	@PostMapping("/add")
	public ResponseVO add(RagQuestion bean) {
		ragQuestionService.add(bean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/addBatch")
	public ResponseVO addBatch(@RequestBody List<RagQuestion> listBean) {
		ragQuestionService.addBatch(listBean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/addOrUpdateBatch")
	public ResponseVO addOrUpdateBatch(@RequestBody List<RagQuestion> listBean) {
		ragQuestionService.addBatch(listBean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/getRagQuestionByQuestionId")
	public ResponseVO getRagQuestionByQuestionId(Integer questionId) {
		return getSuccessResponseVO(ragQuestionService.getRagQuestionByQuestionId(questionId));
	}

	@PostMapping("/updateRagQuestionByQuestionId")
	public ResponseVO updateRagQuestionByQuestionId(RagQuestion bean,Integer questionId) {
		ragQuestionService.updateRagQuestionByQuestionId(bean,questionId);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/deleteRagQuestionByQuestionId")
	public ResponseVO deleteRagQuestionByQuestionId(Integer questionId) {
		ragQuestionService.deleteRagQuestionByQuestionId(questionId);
		return getSuccessResponseVO(null);
	}
}
