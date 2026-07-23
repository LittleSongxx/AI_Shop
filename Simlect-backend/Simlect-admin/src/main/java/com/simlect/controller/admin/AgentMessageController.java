package com.simlect.controller.admin;

import java.util.HashMap;
import java.util.Map;

import com.simlect.entity.query.AgentMessageQuery;
import com.simlect.entity.vo.ResponseVO;
import com.simlect.biz.AgentMessageService;
import com.simlect.component.RedisComponent;
import com.simlect.exception.BusinessException;
import com.simlect.utils.AuthCookieHelper;
import com.simlect.utils.StringTools;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;

@RestController("agentMessageController")
@RequestMapping("/admin/agentMessage")
public class AgentMessageController extends com.simlect.controller.admin.ABaseController {

	@Resource
	private AgentMessageService agentMessageService;

	@Resource
	private RedisComponent redisComponent;

	@Resource
	private AuthCookieHelper authCookieHelper;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(AgentMessageQuery query) {
		return getSuccessResponseVO(agentMessageService.findListByPage(query));
	}

	@PostMapping("/deleteAgentMessageByMessageId")
	public ResponseVO deleteAgentMessageByMessageId(Integer messageId) {
		agentMessageService.deleteAgentMessageByMessageId(messageId);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/supportQueue")
	public ResponseVO supportQueue(Integer pageNo, Integer pageSize) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"supportQueue", page(pageNo, pageSize)));
	}

	@PostMapping("/supportSessions")
	public ResponseVO supportSessions(Integer pageNo, Integer pageSize, String status, String userId) {
		Map<String, Object> body = page(pageNo, pageSize);
		putIfText(body, "status", status);
		putIfText(body, "userId", userId);
		return getSuccessResponseVO(agentMessageService.callSupport("supportSessions", body));
	}

	@PostMapping("/supportClaim")
	public ResponseVO supportClaim(String sessionId, HttpServletRequest request) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"supportClaim", supportBody(sessionId, request)));
	}

	@PostMapping("/supportActivate")
	public ResponseVO supportActivate(String sessionId, HttpServletRequest request) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"supportActivate", supportBody(sessionId, request)));
	}

	@PostMapping("/supportReply")
	public ResponseVO supportReply(String sessionId, String content, HttpServletRequest request) {
		if (StringTools.isEmpty(content)) {
			throw new BusinessException("回复内容不能为空");
		}
		Map<String, Object> body = supportBody(sessionId, request);
		body.put("content", content.trim());
		return getSuccessResponseVO(agentMessageService.callSupport("supportReply", body));
	}

	@PostMapping("/supportResolve")
	public ResponseVO supportResolve(
			String sessionId, String remark, HttpServletRequest request) {
		Map<String, Object> body = supportBody(sessionId, request);
		putIfText(body, "remark", remark);
		return getSuccessResponseVO(agentMessageService.callSupport("supportResolve", body));
	}

	@PostMapping("/supportReturnAi")
	public ResponseVO supportReturnAi(String sessionId, HttpServletRequest request) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"supportReturnAi", supportBody(sessionId, request)));
	}

	@PostMapping("/supportHistory")
	public ResponseVO supportHistory(String sessionId, Integer limit) {
		Map<String, Object> body = new HashMap<>();
		body.put("sessionId", requireSessionId(sessionId));
		body.put("limit", limit == null ? 100 : limit);
		return getSuccessResponseVO(agentMessageService.callSupport("supportHistory", body));
	}

	@PostMapping("/badcases")
	public ResponseVO badcases(Integer pageNo, Integer pageSize, String status) {
		Map<String, Object> body = page(pageNo, pageSize);
		putIfText(body, "status", status);
		return getSuccessResponseVO(agentMessageService.callSupport("badcases", body));
	}

	@PostMapping("/reviewBadcase")
	public ResponseVO reviewBadcase(
			Long candidateId,
			String status,
			String faqAnswer,
			String remark,
			HttpServletRequest request) {
		if (candidateId == null) {
			throw new BusinessException("candidateId不能为空");
		}
		Map<String, Object> body = new HashMap<>();
		body.put("candidateId", candidateId);
		body.put("status", status);
		body.put("faqAnswer", faqAnswer);
		body.put("remark", remark);
		body.put("reviewer", currentAdmin(request));
		return getSuccessResponseVO(agentMessageService.callSupport("reviewBadcase", body));
	}

	private Map<String, Object> supportBody(String sessionId, HttpServletRequest request) {
		Map<String, Object> body = new HashMap<>();
		body.put("sessionId", requireSessionId(sessionId));
		body.put("adminId", currentAdmin(request));
		return body;
	}

	private String currentAdmin(HttpServletRequest request) {
		String token = authCookieHelper.resolveAdminToken(request);
		Object account = redisComponent.getLoginInfo4Admin(token);
		if (account == null || StringTools.isEmpty(String.valueOf(account))) {
			throw new BusinessException("管理员登录已失效");
		}
		return String.valueOf(account);
	}

	private String requireSessionId(String sessionId) {
		if (StringTools.isEmpty(sessionId)) {
			throw new BusinessException("sessionId不能为空");
		}
		return sessionId.trim();
	}

	private Map<String, Object> page(Integer pageNo, Integer pageSize) {
		Map<String, Object> body = new HashMap<>();
		body.put("pageNo", pageNo == null ? 1 : Math.max(1, pageNo));
		body.put("pageSize", pageSize == null ? 30 : Math.min(100, Math.max(1, pageSize)));
		return body;
	}

	private void putIfText(Map<String, Object> body, String key, String value) {
		if (!StringTools.isEmpty(value)) {
			body.put(key, value.trim());
		}
	}
}
