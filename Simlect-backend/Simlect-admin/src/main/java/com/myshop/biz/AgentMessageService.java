package com.myshop.biz;

import com.myshop.entity.query.AgentMessageQuery;
import com.myshop.entity.po.AgentMessage;
import com.myshop.entity.vo.PaginationResultVO;

public interface AgentMessageService {

	PaginationResultVO<AgentMessage> findListByPage(AgentMessageQuery param);

	Integer deleteAgentMessageByMessageId(Integer messageId);
}
