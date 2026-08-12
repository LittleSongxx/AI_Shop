package com.aishop.biz;

import java.util.Map;

import com.aishop.entity.query.AgentMessageQuery;
import com.aishop.entity.po.AgentMessage;
import com.aishop.entity.vo.PaginationResultVO;

public interface AgentMessageService {

	PaginationResultVO<AgentMessage> findListByPage(AgentMessageQuery param);

	Integer deleteAgentMessageByMessageId(Integer messageId);

	Object callSupport(String action, Map<String, Object> body);

	byte[] callReport(String action, Map<String, Object> body);
}
