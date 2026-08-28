package com.aishop.biz;

import java.util.Map;

import org.springframework.http.ResponseEntity;

import com.aishop.entity.query.AgentMessageQuery;
import com.aishop.entity.po.AgentMessage;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.entity.vo.ResponseVO;

public interface AgentMessageService {

	PaginationResultVO<AgentMessage> findListByPage(AgentMessageQuery param);

	Integer deleteAgentMessageByMessageId(Integer messageId);

	Object callSupport(String action, Map<String, Object> body);

	ResponseEntity<ResponseVO<Object>> callDataAnalyst(String action, Map<String, Object> body);

	byte[] callReport(String action, Map<String, Object> body);

	ResponseEntity<byte[]> callDataAnalystReport(String action, Map<String, Object> body);
}
