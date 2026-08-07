package com.aishop.controller.admin;

import java.util.HashMap;
import java.util.Map;

import com.aishop.entity.query.AgentMessageQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.AgentMessageService;
import com.aishop.component.RedisComponent;
import com.aishop.exception.BusinessException;
import com.aishop.utils.AuthCookieHelper;
import com.aishop.utils.JsonUtils;
import com.aishop.utils.StringTools;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;

@RestController("agentMessageController")
@RequestMapping("/admin/agentMessage")
public class AgentMessageController extends com.aishop.controller.admin.ABaseController {

	@Resource
	private AgentMessageService agentMessageService;

	@Resource
	private RedisComponent redisComponent;

	@Resource
	private AuthCookieHelper authCookieHelper;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(AgentMessageQuery query) {
		query.setOrderBy(com.aishop.entity.query.SafeSort.of("send_time desc"));
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

	@PostMapping("/supportStats")
	public ResponseVO supportStats(Integer windowHours) {
		Map<String, Object> body = new HashMap<>();
		body.put("windowHours", windowHours == null ? 24 : windowHours);
		return getSuccessResponseVO(agentMessageService.callSupport("supportStats", body));
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

	@PostMapping("/supportCases")
	public ResponseVO supportCases(Integer pageNo, Integer pageSize, String status, String userId) {
		Map<String, Object> body = page(pageNo, pageSize);
		putIfText(body, "status", status);
		putIfText(body, "userId", userId);
		return getSuccessResponseVO(agentMessageService.callSupport("supportCases", body));
	}

	@PostMapping("/supportCaseDetail")
	public ResponseVO supportCaseDetail(String caseId) {
		Map<String, Object> body = new HashMap<>();
		if (StringTools.isEmpty(caseId)) {
			throw new BusinessException("caseId不能为空");
		}
		body.put("caseId", caseId.trim());
		return getSuccessResponseVO(agentMessageService.callSupport("supportCaseDetail", body));
	}

	@PostMapping("/supportCaseClaim")
	public ResponseVO supportCaseClaim(String caseId, HttpServletRequest request) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"supportCaseClaim", caseBody(caseId, request)));
	}

	@PostMapping("/supportCaseInProgress")
	public ResponseVO supportCaseInProgress(String caseId, HttpServletRequest request) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"supportCaseInProgress", caseBody(caseId, request)));
	}

	@PostMapping("/supportCaseResolve")
	public ResponseVO supportCaseResolve(
			String caseId, String supportSessionId, String resolutionCode,
			String rootCause, String resolutionSummary, HttpServletRequest request) {
		Map<String, Object> body = caseBody(caseId, request);
		putIfText(body, "supportSessionId", supportSessionId);
		putIfText(body, "resolutionCode", resolutionCode);
		putIfText(body, "rootCause", rootCause);
		putIfText(body, "resolutionSummary", resolutionSummary);
		return getSuccessResponseVO(agentMessageService.callSupport("supportCaseResolve", body));
	}

	@PostMapping("/traceRuns")
	public ResponseVO traceRuns(Integer pageNo, Integer pageSize, String status,
			String intent, String userId, String outcome) {
		Map<String, Object> body = page(pageNo, pageSize);
		putIfText(body, "status", status);
		putIfText(body, "intent", intent);
		putIfText(body, "userId", userId);
		putIfText(body, "outcome", outcome);
		return getSuccessResponseVO(agentMessageService.callSupport("traceRuns", body));
	}

	@PostMapping("/traceDetail")
	public ResponseVO traceDetail(String runId) {
		if (StringTools.isEmpty(runId)) {
			throw new BusinessException("runId不能为空");
		}
		return getSuccessResponseVO(agentMessageService.callSupport(
				"traceDetail", Map.of("runId", runId.trim())));
	}

	@PostMapping("/reviewEpisode")
	public ResponseVO reviewEpisode(
			String runId, String datasetEligible, String note, HttpServletRequest request) {
		if (StringTools.isEmpty(runId)) {
			throw new BusinessException("runId不能为空");
		}
		if (StringTools.isEmpty(datasetEligible)) {
			throw new BusinessException("datasetEligible不能为空");
		}
		Map<String, Object> body = new HashMap<>();
		body.put("runId", runId.trim());
		body.put("datasetEligible", datasetEligible.trim());
		putIfText(body, "note", note);
		// Reviewer identity is always derived from the authenticated admin session.
		body.put("reviewer", currentAdmin(request));
		return getSuccessResponseVO(agentMessageService.callSupport("reviewEpisode", body));
	}

	private Map<String, Object> caseBody(String caseId, HttpServletRequest request) {
		if (StringTools.isEmpty(caseId)) {
			throw new BusinessException("caseId不能为空");
		}
		Map<String, Object> body = new HashMap<>();
		body.put("caseId", caseId.trim());
		// Never trust an adminId sent by the browser. The authenticated admin
		// account is the only identity forwarded to the Agent service.
		body.put("adminId", currentAdmin(request));
		return body;
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
			String labels,
			String owner,
			String fixVersion,
			String regression,
			HttpServletRequest request) {
		if (candidateId == null) {
			throw new BusinessException("candidateId不能为空");
		}
		Map<String, Object> body = new HashMap<>();
		body.put("candidateId", candidateId);
		body.put("status", status);
		body.put("faqAnswer", faqAnswer);
		body.put("remark", remark);
		putJsonIfPresent(body, "labels", labels, java.util.List.class);
		putIfText(body, "owner", owner);
		putIfText(body, "fixVersion", fixVersion);
		putJsonIfPresent(body, "regression", regression, Map.class);
		body.put("reviewer", currentAdmin(request));
		return getSuccessResponseVO(agentMessageService.callSupport("reviewBadcase", body));
	}

	@PostMapping("/regressionCases")
	public ResponseVO regressionCases(Integer pageNo, Integer pageSize, String status) {
		Map<String, Object> body = page(pageNo, pageSize);
		putIfText(body, "status", status);
		return getSuccessResponseVO(agentMessageService.callSupport("regressionCases", body));
	}

	@PostMapping("/runRegressionCases")
	public ResponseVO runRegressionCases(Long caseId) {
		Map<String, Object> body = new HashMap<>();
		if (caseId != null) {
			body.put("caseId", caseId);
		}
		return getSuccessResponseVO(agentMessageService.callSupport("runRegressionCases", body));
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

	private void putJsonIfPresent(
			Map<String, Object> body, String key, String value, Class<?> expectedType) {
		if (StringTools.isEmpty(value)) {
			return;
		}
		try {
			Object parsed = JsonUtils.parse(value.trim());
			if (!expectedType.isInstance(parsed)) {
				throw new BusinessException(key + "格式无效");
			}
			body.put(key, parsed);
		} catch (IllegalStateException e) {
			throw new BusinessException(key + "格式无效");
		}
	}
}
