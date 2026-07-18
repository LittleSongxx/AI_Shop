package com.myshop.biz.impl;

import java.util.Date;
import java.util.List;

import com.myshop.component.SpringContext;
import com.myshop.constants.Constants;
import com.myshop.constants.RabbitMQConfig;
import com.myshop.constants.ReliableMessageSender;
import com.myshop.support.MqIdempotencyKeys;
import com.myshop.entity.dto.RagDataDTO;
import com.myshop.entity.enums.MessageReliabilityLevelEnum;
import com.myshop.entity.enums.RagDataTypeEnum;
import com.myshop.exception.BusinessException;
import jakarta.annotation.Resource;

import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import com.myshop.entity.enums.PageSize;
import com.myshop.entity.query.RagQuestionQuery;
import com.myshop.entity.po.RagQuestion;
import com.myshop.entity.vo.PaginationResultVO;
import com.myshop.entity.query.SimplePage;
import com.myshop.mappers.RagQuestionMapper;
import com.myshop.biz.RagQuestionService;
import com.myshop.utils.StringTools;

@Service("ragQuestionService")
@Slf4j
public class RagQuestionServiceImpl implements RagQuestionService {

	@Resource
	private RagQuestionMapper<RagQuestion, RagQuestionQuery> ragQuestionMapper;
	@Resource
	private ReliableMessageSender reliableMessageSender;

	@Override
	public List<RagQuestion> findListByParam(RagQuestionQuery param) {
		return this.ragQuestionMapper.selectList(param);
	}

	@Override
	public Integer findCountByParam(RagQuestionQuery param) {
		return this.ragQuestionMapper.selectCount(param);
	}

	@Override
	public PaginationResultVO<RagQuestion> findListByPage(RagQuestionQuery param) {
		int count = this.findCountByParam(param);
		int pageSize = param.getPageSize() == null ? PageSize.SIZE15.getSize() : param.getPageSize();

		SimplePage page = new SimplePage(param.getPageNo(), count, pageSize);
		param.setSimplePage(page);
		List<RagQuestion> list = this.findListByParam(param);
		PaginationResultVO<RagQuestion> result = new PaginationResultVO(count, page.getPageSize(), page.getPageNo(), page.getPageTotal(), list);
		return result;
	}

	@Override
	public Integer add(RagQuestion bean) {
		return this.ragQuestionMapper.insert(bean);
	}

	@Override
	public Integer addBatch(List<RagQuestion> listBean) {
		if (listBean == null || listBean.isEmpty()) {
			return 0;
		}
		return this.ragQuestionMapper.insertBatch(listBean);
	}

	@Override
	public Integer addOrUpdateBatch(List<RagQuestion> listBean) {
		if (listBean == null || listBean.isEmpty()) {
			return 0;
		}
		return this.ragQuestionMapper.insertOrUpdateBatch(listBean);
	}

	@Override
	public Integer updateByParam(RagQuestion bean, RagQuestionQuery param) {
		StringTools.checkParam(param);
		return this.ragQuestionMapper.updateByParam(bean, param);
	}

	@Override
	public Integer deleteByParam(RagQuestionQuery param) {
		StringTools.checkParam(param);
		return this.ragQuestionMapper.deleteByParam(param);
	}

	@Override
	public RagQuestion getRagQuestionByQuestionId(Integer questionId) {
		return this.ragQuestionMapper.selectByQuestionId(questionId);
	}

	@Override
	public Integer updateRagQuestionByQuestionId(RagQuestion bean, Integer questionId) {
		return this.ragQuestionMapper.updateByQuestionId(bean, questionId);
	}

	@Override
	public Integer deleteRagQuestionByQuestionId(Integer questionId) {
		// 先从数据库删除
		Integer result = this.ragQuestionMapper.deleteByQuestionId(questionId);

		// 如果删除成功,再加入队列通知向量库删除
		if (result > 0) {
			RagDataDTO ragDataDTO = new RagDataDTO(questionId.toString(), RagDataTypeEnum.FAQ.getType());
			reliableMessageSender.sendMessage(
					RabbitMQConfig.RAG_EXCHANGE,
					RabbitMQConfig.RAG_QUEUE_KEY,
					ragDataDTO,
					MqIdempotencyKeys.ragFaq(String.valueOf(questionId)),
					MessageReliabilityLevelEnum.HIGH);
			log.info("已添加删除任务到RAG队列, questionId: {}", questionId);
		}
		return result;
	}

	@Override
	public void saveRagQuestion(Integer questionId, String similarQuestion, String question, String answer) {
		RagQuestion bean = new RagQuestion();
		if (questionId == null){
			bean.setQuestion(question);
			bean.setSimilarQuestion(similarQuestion);
			bean.setAnswer(answer);
			bean.setCreateTime(new Date());
			this.add(bean);
			questionId = bean.getQuestionId();
		}else {
			bean = this.getRagQuestionByQuestionId(questionId);
			if (bean == null){
				throw new BusinessException(" ragQuestionId 不存在");
			}
			bean.setQuestion(question);
			bean.setSimilarQuestion(similarQuestion);
			bean.setAnswer(answer);
			this.updateRagQuestionByQuestionId(bean, questionId);
		}
		RagDataDTO ragDataDTO = new RagDataDTO(questionId.toString(), RagDataTypeEnum.FAQ.getType());
		reliableMessageSender.sendMessage(
				RabbitMQConfig.RAG_EXCHANGE,
				RabbitMQConfig.RAG_QUEUE_KEY,
				ragDataDTO,
				MqIdempotencyKeys.ragFaq(String.valueOf(questionId)),
				MessageReliabilityLevelEnum.HIGH);
	}
}
