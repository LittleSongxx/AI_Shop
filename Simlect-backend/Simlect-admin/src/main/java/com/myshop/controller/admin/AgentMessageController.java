package com.myshop.controller.admin;

import com.myshop.entity.query.AgentMessageQuery;
import com.myshop.entity.vo.ResponseVO;
import com.myshop.biz.AgentMessageService;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("agentMessageController")
@RequestMapping("/admin/agentMessage")
public class AgentMessageController extends com.myshop.controller.admin.ABaseController {

	@Resource
	private AgentMessageService agentMessageService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(AgentMessageQuery query) {
		return getSuccessResponseVO(agentMessageService.findListByPage(query));
	}

	@PostMapping("/deleteAgentMessageByMessageId")
	public ResponseVO deleteAgentMessageByMessageId(Integer messageId) {
		agentMessageService.deleteAgentMessageByMessageId(messageId);
		return getSuccessResponseVO(null);
	}
}
