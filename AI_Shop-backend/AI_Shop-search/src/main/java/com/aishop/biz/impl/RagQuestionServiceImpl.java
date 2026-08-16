package com.aishop.biz.impl;

import java.util.Date;
import java.util.List;
import java.util.Locale;

import com.aishop.biz.KnowledgeBaseService;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.TransactionalMqSender;
import com.aishop.support.MqIdempotencyKeys;
import com.aishop.entity.dto.RagDataDTO;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.enums.RagDataTypeEnum;
import com.aishop.exception.BusinessException;
import jakarta.annotation.Resource;

import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import com.aishop.entity.enums.PageSize;
import com.aishop.entity.query.RagQuestionQuery;
import com.aishop.entity.po.RagQuestion;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.entity.query.SimplePage;
import com.aishop.mappers.RagQuestionMapper;
import com.aishop.biz.RagQuestionService;
import com.aishop.utils.StringTools;

@Service("ragQuestionService")
@Slf4j
public class RagQuestionServiceImpl implements RagQuestionService {

	@Resource
	private RagQuestionMapper<RagQuestion, RagQuestionQuery> ragQuestionMapper;
	@Resource
	private TransactionalMqSender transactionalMqSender;
	@Resource
	private JdbcTemplate jdbcTemplate;
	@Resource
	private KnowledgeBaseService knowledgeBaseService;

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
	@Transactional(rollbackFor = Exception.class)
	public Integer deleteRagQuestionByQuestionId(Integer questionId) {
		// 先从数据库删除
		Integer result = this.ragQuestionMapper.deleteByQuestionId(questionId);

		// 如果删除成功,再加入队列通知向量库删除
		if (result > 0) {
			RagDataDTO ragDataDTO = new RagDataDTO(questionId.toString(), RagDataTypeEnum.FAQ.getType());
			transactionalMqSender.sendAfterCommit(
					RabbitMQConfig.RAG_EXCHANGE,
					RabbitMQConfig.RAG_QUEUE_KEY,
					ragDataDTO,
					MqIdempotencyKeys.ragFaq(String.valueOf(questionId), ragDataDTO.getVersion()),
					MessageReliabilityLevelEnum.HIGH);
			log.info("已添加删除任务到RAG队列, questionId: {}", questionId);
			invalidateCachesAfterCommit();
		}
		return result;
	}

	@Override
	@Transactional(rollbackFor = Exception.class)
	public void saveRagQuestion(RagQuestion input) {
		if (input == null || StringTools.isEmpty(input.getQuestion())
				|| StringTools.isEmpty(input.getAnswer())) {
			throw new BusinessException("问题和答案不能为空");
		}
		Integer questionId = input.getQuestionId();
		RagQuestion bean = new RagQuestion();
		int version = 1;
		if (questionId == null){
			bean.setQuestion(input.getQuestion().trim());
			bean.setSimilarQuestion(input.getSimilarQuestion());
			bean.setAnswer(input.getAnswer().trim());
			bean.setCreateTime(new Date());
			this.add(bean);
			questionId = bean.getQuestionId();
		}else {
			bean = this.getRagQuestionByQuestionId(questionId);
			if (bean == null){
				throw new BusinessException(" ragQuestionId 不存在");
			}
			bean.setQuestion(input.getQuestion().trim());
			bean.setSimilarQuestion(input.getSimilarQuestion());
			bean.setAnswer(input.getAnswer().trim());
			version = (bean.getVersion() == null ? 1 : bean.getVersion()) + 1;
			this.updateRagQuestionByQuestionId(bean, questionId);
		}
		jdbcTemplate.update(
				"""
				UPDATE rag_question
				SET normalized_question=?, category=?, language=?, channel=?, priority=?,
				    version=?, effective_start=?, effective_end=?, publish_status=?,
				    source=?, owner=?, update_time=NOW()
				WHERE question_id=?
				""",
				normalizeQuestion(input.getQuestion()),
				defaultText(input.getCategory(), "general"),
				defaultText(input.getLanguage(), "zh-CN"),
				defaultText(input.getChannel(), "all"),
				input.getPriority() == null ? 0 : input.getPriority(),
				version,
				input.getEffectiveStart(),
				input.getEffectiveEnd(),
				defaultText(input.getPublishStatus(), "PUBLISHED").toUpperCase(Locale.ROOT),
				defaultText(input.getSource(), "ADMIN"),
				input.getOwner(),
				questionId);
		RagDataDTO ragDataDTO = new RagDataDTO(questionId.toString(), RagDataTypeEnum.FAQ.getType());
		transactionalMqSender.sendAfterCommit(
				RabbitMQConfig.RAG_EXCHANGE,
				RabbitMQConfig.RAG_QUEUE_KEY,
				ragDataDTO,
				MqIdempotencyKeys.ragFaq(String.valueOf(questionId), ragDataDTO.getVersion()),
				MessageReliabilityLevelEnum.HIGH);
		invalidateCachesAfterCommit();
	}

	private void invalidateCachesAfterCommit() {
		if (TransactionSynchronizationManager.isSynchronizationActive()) {
			TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
				@Override
				public void afterCommit() {
					knowledgeBaseService.invalidateCaches();
				}
			});
			return;
		}
		knowledgeBaseService.invalidateCaches();
	}

	private String normalizeQuestion(String value) {
		return value == null ? "" : value.trim().toLowerCase(Locale.ROOT)
				.replaceAll("[\\s，。！？、,.!?;；:：\"'（）()\\[\\]【】]+", "");
	}

	private String defaultText(String value, String defaultValue) {
		return StringTools.isEmpty(value) ? defaultValue : value.trim();
	}
}
