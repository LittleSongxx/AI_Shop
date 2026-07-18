package com.myshop.biz;

import java.util.List;

import com.myshop.entity.query.RagQuestionQuery;
import com.myshop.entity.po.RagQuestion;
import com.myshop.entity.vo.PaginationResultVO;

public interface RagQuestionService {

	List<RagQuestion> findListByParam(RagQuestionQuery param);

	Integer findCountByParam(RagQuestionQuery param);

	PaginationResultVO<RagQuestion> findListByPage(RagQuestionQuery param);

	Integer add(RagQuestion bean);

	Integer addBatch(List<RagQuestion> listBean);

	Integer addOrUpdateBatch(List<RagQuestion> listBean);

	Integer updateByParam(RagQuestion bean,RagQuestionQuery param);

	Integer deleteByParam(RagQuestionQuery param);

	RagQuestion getRagQuestionByQuestionId(Integer questionId);

	Integer updateRagQuestionByQuestionId(RagQuestion bean,Integer questionId);

	Integer deleteRagQuestionByQuestionId(Integer questionId);

    void saveRagQuestion(Integer questionId, String similarQuestion, String question, String answer);
}
