package com.aishop.controller.admin;

import java.util.HashMap;
import java.util.Map;

import com.aishop.entity.query.AgentMessageQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.AgentMessageService;
import com.aishop.constants.AdminPermissions;
import com.aishop.security.AdminSecurityContext;
import com.aishop.security.RequireAdminPermission;
import com.aishop.exception.BusinessException;
import com.aishop.utils.JsonUtils;
import com.aishop.utils.StringTools;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;

import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;

@RestController("agentMessageController")
@RequestMapping("/admin/agentMessage")
public class AgentMessageController extends com.aishop.controller.admin.ABaseController {

	@Resource
	private AgentMessageService agentMessageService;

	@PostMapping("/loadDataList")
	@RequireAdminPermission(value = {AdminPermissions.SUPPORT_READ, AdminPermissions.AUDIT_READ}, requireAll = false)
	public ResponseVO loadDataList(AgentMessageQuery query) {
		query.setOrderBy(com.aishop.entity.query.SafeSort.of("send_time desc"));
		return getSuccessResponseVO(agentMessageService.findListByPage(query));
	}

	@PostMapping("/deleteAgentMessageByMessageId")
	@RequireAdminPermission(AdminPermissions.SUPPORT_WRITE)
	public ResponseVO deleteAgentMessageByMessageId(Integer messageId) {
		agentMessageService.deleteAgentMessageByMessageId(messageId);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/supportQueue")
	@RequireAdminPermission(AdminPermissions.SUPPORT_READ)
	public ResponseVO supportQueue(Integer pageNo, Integer pageSize) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"supportQueue", page(pageNo, pageSize)));
	}

	@PostMapping("/supportSessions")
	@RequireAdminPermission(value = {AdminPermissions.SUPPORT_READ, AdminPermissions.AUDIT_READ}, requireAll = false)
	public ResponseVO supportSessions(Integer pageNo, Integer pageSize, String status, String userId) {
		Map<String, Object> body = page(pageNo, pageSize);
		putIfText(body, "status", status);
		putIfText(body, "userId", userId);
		return getSuccessResponseVO(agentMessageService.callSupport("supportSessions", body));
	}

	@PostMapping("/supportStats")
	@RequireAdminPermission(value = {AdminPermissions.SUPPORT_READ, AdminPermissions.ANALYTICS_READ}, requireAll = false)
	public ResponseVO supportStats(Integer windowHours) {
		Map<String, Object> body = new HashMap<>();
		body.put("windowHours", windowHours == null ? 24 : windowHours);
		return getSuccessResponseVO(agentMessageService.callSupport("supportStats", body));
	}

	@PostMapping("/supportClaim")
	@RequireAdminPermission(AdminPermissions.SUPPORT_WRITE)
	public ResponseVO supportClaim(String sessionId, HttpServletRequest request) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"supportClaim", supportBody(sessionId, request)));
	}

	@PostMapping("/supportActivate")
	@RequireAdminPermission(AdminPermissions.SUPPORT_WRITE)
	public ResponseVO supportActivate(String sessionId, HttpServletRequest request) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"supportActivate", supportBody(sessionId, request)));
	}

	@PostMapping("/supportReply")
	@RequireAdminPermission(AdminPermissions.SUPPORT_WRITE)
	public ResponseVO supportReply(String sessionId, String content, HttpServletRequest request) {
		if (StringTools.isEmpty(content)) {
			throw new BusinessException("回复内容不能为空");
		}
		Map<String, Object> body = supportBody(sessionId, request);
		body.put("content", content.trim());
		return getSuccessResponseVO(agentMessageService.callSupport("supportReply", body));
	}

	@PostMapping("/supportResolve")
	@RequireAdminPermission(AdminPermissions.SUPPORT_WRITE)
	public ResponseVO supportResolve(
			String sessionId, String remark, HttpServletRequest request) {
		Map<String, Object> body = supportBody(sessionId, request);
		putIfText(body, "remark", remark);
		return getSuccessResponseVO(agentMessageService.callSupport("supportResolve", body));
	}

	@PostMapping("/supportReturnAi")
	@RequireAdminPermission(AdminPermissions.SUPPORT_WRITE)
	public ResponseVO supportReturnAi(String sessionId, HttpServletRequest request) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"supportReturnAi", supportBody(sessionId, request)));
	}

	@PostMapping("/supportHistory")
	@RequireAdminPermission(value = {AdminPermissions.SUPPORT_READ, AdminPermissions.AUDIT_READ}, requireAll = false)
	public ResponseVO supportHistory(String sessionId, Integer limit) {
		Map<String, Object> body = new HashMap<>();
		body.put("sessionId", requireSessionId(sessionId));
		body.put("limit", limit == null ? 100 : limit);
		return getSuccessResponseVO(agentMessageService.callSupport("supportHistory", body));
	}

	@PostMapping("/supportCases")
	@RequireAdminPermission(value = {AdminPermissions.SUPPORT_READ, AdminPermissions.AUDIT_READ}, requireAll = false)
	public ResponseVO supportCases(Integer pageNo, Integer pageSize, String status, String userId) {
		Map<String, Object> body = page(pageNo, pageSize);
		putIfText(body, "status", status);
		putIfText(body, "userId", userId);
		return getSuccessResponseVO(agentMessageService.callSupport("supportCases", body));
	}

	@PostMapping("/supportCaseDetail")
	@RequireAdminPermission(value = {AdminPermissions.SUPPORT_READ, AdminPermissions.AUDIT_READ}, requireAll = false)
	public ResponseVO supportCaseDetail(String caseId) {
		Map<String, Object> body = new HashMap<>();
		if (StringTools.isEmpty(caseId)) {
			throw new BusinessException("caseId不能为空");
		}
		body.put("caseId", caseId.trim());
		return getSuccessResponseVO(agentMessageService.callSupport("supportCaseDetail", body));
	}

	@PostMapping("/supportCaseClaim")
	@RequireAdminPermission(AdminPermissions.SUPPORT_WRITE)
	public ResponseVO supportCaseClaim(String caseId, HttpServletRequest request) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"supportCaseClaim", caseBody(caseId, request)));
	}

	@PostMapping("/supportCaseInProgress")
	@RequireAdminPermission(AdminPermissions.SUPPORT_WRITE)
	public ResponseVO supportCaseInProgress(String caseId, HttpServletRequest request) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"supportCaseInProgress", caseBody(caseId, request)));
	}

	@PostMapping("/supportCaseResolve")
	@RequireAdminPermission(AdminPermissions.SUPPORT_WRITE)
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
	@RequireAdminPermission(value = {AdminPermissions.AI_EVALUATE, AdminPermissions.ANALYTICS_READ, AdminPermissions.AUDIT_READ}, requireAll = false)
	public ResponseVO traceRuns(Integer pageNo, Integer pageSize, String status,
			String intent, String userId, String outcome, String agentId, String runScope) {
		Map<String, Object> body = page(pageNo, pageSize);
		putIfText(body, "status", status);
		putIfText(body, "intent", intent);
		putIfText(body, "userId", userId);
		putIfText(body, "outcome", outcome);
		putIfText(body, "agentId", agentId);
		putIfText(body, "runScope", runScope);
		return getSuccessResponseVO(agentMessageService.callSupport("traceRuns", body));
	}

	@PostMapping("/dataAnalyst/ask")
	@RequireAdminPermission(AdminPermissions.ANALYTICS_READ)
	public ResponseEntity<ResponseVO<Object>> dataAnalystAsk(
			String question, String cursor, Integer pageSize, String tenantId) {
		if (StringTools.isEmpty(cursor)
				&& (StringTools.isEmpty(question) || question.trim().length() > 500)) {
			throw new BusinessException("问题不能为空且不能超过500字");
		}
		Map<String, Object> body = new HashMap<>();
		putIfText(body, "question", question);
		putIfText(body, "cursor", cursor);
		putIfText(body, "tenantId", tenantId);
		if (pageSize != null) {
			body.put("pageSize", pageSize);
		}
		return agentMessageService.callDataAnalyst("dataAnalyst/ask", body);
	}

	@PostMapping("/dataAnalyst/clarify")
	@RequireAdminPermission(AdminPermissions.ANALYTICS_READ)
	public ResponseEntity<ResponseVO<Object>> dataAnalystClarify(
			String clarificationToken, String choiceId, Integer pageSize, String tenantId) {
		if (StringTools.isEmpty(clarificationToken) || StringTools.isEmpty(choiceId)) {
			throw new BusinessException("clarificationToken 和 choiceId 不能为空");
		}
		Map<String, Object> body = new HashMap<>();
		body.put("clarificationToken", clarificationToken.trim());
		body.put("choiceId", choiceId.trim());
		putIfText(body, "tenantId", tenantId);
		if (pageSize != null) {
			body.put("pageSize", pageSize);
		}
		return agentMessageService.callDataAnalyst("dataAnalyst/clarify", body);
	}

	@PostMapping("/dataAnalyst/page")
	@RequireAdminPermission(AdminPermissions.ANALYTICS_READ)
	public ResponseEntity<ResponseVO<Object>> dataAnalystPage(
			String cursor, Integer pageSize, String tenantId) {
		if (StringTools.isEmpty(cursor)) {
			throw new BusinessException("cursor 不能为空");
		}
		Map<String, Object> body = new HashMap<>();
		body.put("cursor", cursor.trim());
		putIfText(body, "tenantId", tenantId);
		if (pageSize != null) {
			body.put("pageSize", pageSize);
		}
		return agentMessageService.callDataAnalyst("dataAnalyst/page", body);
	}

	@PostMapping("/dataAnalyst/export")
	@RequireAdminPermission(AdminPermissions.ANALYTICS_EXPORT)
	public ResponseEntity<ResponseVO<Object>> dataAnalystExport(
			String resultSetId, String question, String tenantId) {
		Map<String, Object> body = new HashMap<>();
		putIfText(body, "resultSetId", resultSetId);
		// Kept only so a legacy question-only request reaches Agent and receives
		// the stable HTTP 400 / RESULT_SET_ID_REQUIRED contract.
		putIfText(body, "question", question);
		putIfText(body, "tenantId", tenantId);
		return agentMessageService.callDataAnalyst("dataAnalyst/export", body);
	}

	@PostMapping("/dataAnalyst/export/status")
	@RequireAdminPermission(AdminPermissions.ANALYTICS_EXPORT)
	public ResponseEntity<ResponseVO<Object>> dataAnalystExportStatus(
			String jobId, String tenantId) {
		if (StringTools.isEmpty(jobId)) {
			throw new BusinessException("jobId不能为空");
		}
		Map<String, Object> body = new HashMap<>();
		body.put("jobId", jobId.trim());
		putIfText(body, "tenantId", tenantId);
		return agentMessageService.callDataAnalyst("dataAnalyst/export/status", body);
	}

	@PostMapping("/dataAnalyst/export/download")
	@RequireAdminPermission(AdminPermissions.ANALYTICS_EXPORT)
	public ResponseEntity<byte[]> dataAnalystExportDownload(String jobId, String tenantId) {
		if (StringTools.isEmpty(jobId)) {
			throw new BusinessException("jobId不能为空");
		}
		Map<String, Object> body = new HashMap<>();
		body.put("jobId", jobId.trim());
		putIfText(body, "tenantId", tenantId);
		ResponseEntity<byte[]> upstream = agentMessageService.callDataAnalystReport(
				"dataAnalyst/export/download", body);
		if (!upstream.getStatusCode().is2xxSuccessful()) {
			return upstream;
		}
		ContentDisposition disposition = ContentDisposition.attachment()
				.filename(jobId.trim() + ".json")
				.build();
		return ResponseEntity.status(upstream.getStatusCode())
				.headers(upstream.getHeaders())
				.contentType(MediaType.APPLICATION_JSON)
				.header(HttpHeaders.CONTENT_DISPOSITION, disposition.toString())
				.body(upstream.getBody());
	}

	@PostMapping("/inventoryOps/suggestions")
	@RequireAdminPermission(AdminPermissions.AI_CONFIG)
	public ResponseVO inventoryOpsSuggestions(
			Integer lookbackDays, Integer limit, HttpServletRequest request) {
		Map<String, Object> body = new HashMap<>();
		body.put("adminId", currentAdmin(request));
		body.put("lookbackDays", lookbackDays == null ? 30 : lookbackDays);
		body.put("limit", limit == null ? 50 : limit);
		return getSuccessResponseVO(agentMessageService.callSupport(
				"inventoryOps/suggestions", body));
	}

	@PostMapping("/traceDetail")
	@RequireAdminPermission(value = {AdminPermissions.AI_EVALUATE, AdminPermissions.AUDIT_READ}, requireAll = false)
	public ResponseVO traceDetail(String runId) {
		if (StringTools.isEmpty(runId)) {
			throw new BusinessException("runId不能为空");
		}
		return getSuccessResponseVO(agentMessageService.callSupport(
				"traceDetail", Map.of("runId", runId.trim())));
	}

	@PostMapping("/reviewEpisode")
	@RequireAdminPermission(AdminPermissions.AI_EVALUATE)
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
	@RequireAdminPermission(value = {AdminPermissions.AI_EVALUATE, AdminPermissions.AUDIT_READ}, requireAll = false)
	public ResponseVO badcases(Integer pageNo, Integer pageSize, String status) {
		Map<String, Object> body = page(pageNo, pageSize);
		putIfText(body, "status", status);
		return getSuccessResponseVO(agentMessageService.callSupport("badcases", body));
	}

	@PostMapping("/reviewBadcase")
	@RequireAdminPermission(AdminPermissions.AI_EVALUATE)
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
	@RequireAdminPermission(value = {AdminPermissions.AI_EVALUATE, AdminPermissions.AUDIT_READ}, requireAll = false)
	public ResponseVO regressionCases(Integer pageNo, Integer pageSize, String status) {
		Map<String, Object> body = page(pageNo, pageSize);
		putIfText(body, "status", status);
		return getSuccessResponseVO(agentMessageService.callSupport("regressionCases", body));
	}

	@PostMapping("/runRegressionCases")
	@RequireAdminPermission(AdminPermissions.AI_EVALUATE)
	public ResponseVO runRegressionCases(Long caseId) {
		Map<String, Object> body = new HashMap<>();
		if (caseId != null) {
			body.put("caseId", caseId);
		}
		return getSuccessResponseVO(agentMessageService.callSupport("runRegressionCases", body));
	}

	@PostMapping("/pilotBatches/create")
	@RequireAdminPermission(AdminPermissions.AI_PILOT)
	public ResponseVO createPilotBatch(
			String name, String description, String evidenceSource, String consentTextVersion) {
		Map<String, Object> body = new HashMap<>();
		body.put("name", requireText(name, "name"));
		putIfText(body, "description", description);
		body.put("evidenceSource", requireText(evidenceSource, "evidenceSource"));
		body.put("consentTextVersion", requireText(consentTextVersion, "consentTextVersion"));
		return getSuccessResponseVO(agentMessageService.callSupport("createPilotBatch", body));
	}

	@PostMapping("/pilotBatches")
	@RequireAdminPermission(value = {
			AdminPermissions.AI_PILOT,
			AdminPermissions.ANALYTICS_READ,
			AdminPermissions.AUDIT_READ
	}, requireAll = false)
	public ResponseVO pilotBatches(String status, Integer limit) {
		Map<String, Object> body = new HashMap<>();
		putIfText(body, "status", status);
		body.put("limit", limit == null ? 50 : Math.min(100, Math.max(1, limit)));
		return getSuccessResponseVO(agentMessageService.callSupport("pilotBatches", body));
	}

	@PostMapping("/pilotBatches/start")
	@RequireAdminPermission(AdminPermissions.AI_PILOT)
	public ResponseVO startPilotBatch(String batchId) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"startPilotBatch", Map.of("batchId", requireText(batchId, "batchId"))));
	}

	@PostMapping("/pilotBatches/close")
	@RequireAdminPermission(AdminPermissions.AI_PILOT)
	public ResponseVO closePilotBatch(String batchId) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"closePilotBatch", Map.of("batchId", requireText(batchId, "batchId"))));
	}

	@PostMapping("/pilotBatches/participants/register")
	@RequireAdminPermission(AdminPermissions.AI_PILOT)
	public ResponseVO registerPilotParticipant(
			String batchId, String userId, String pseudonym) {
		Map<String, Object> body = new HashMap<>();
		body.put("batchId", requireText(batchId, "batchId"));
		body.put("userId", requireText(userId, "userId"));
		putIfText(body, "pseudonym", pseudonym);
		return getSuccessResponseVO(agentMessageService.callSupport(
				"registerPilotParticipant", body));
	}

	@PostMapping("/pilotBatches/participants/withdraw")
	@RequireAdminPermission(AdminPermissions.AI_PILOT)
	public ResponseVO withdrawPilotParticipant(String batchId, String participantId) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"withdrawPilotParticipant",
				Map.of(
						"batchId", requireText(batchId, "batchId"),
						"participantId", requireText(participantId, "participantId"))));
	}

	@PostMapping("/pilotBatches/participants")
	@RequireAdminPermission(value = {
			AdminPermissions.AI_PILOT, AdminPermissions.AUDIT_READ
	}, requireAll = false)
	public ResponseVO pilotParticipants(String batchId) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"pilotParticipants", Map.of("batchId", requireText(batchId, "batchId"))));
	}

	@PostMapping("/metrics/overview")
	@RequireAdminPermission(value = {
			AdminPermissions.ANALYTICS_READ, AdminPermissions.AI_EVALUATE
	}, requireAll = false)
	public ResponseVO metricsOverview(
			String batchId, String evidenceSource, String startAt, String endAt) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"metricsOverview", metricBody(batchId, evidenceSource, startAt, endAt)));
	}

	@PostMapping("/metrics/performance")
	@RequireAdminPermission(value = {
			AdminPermissions.ANALYTICS_READ, AdminPermissions.AI_EVALUATE
	}, requireAll = false)
	public ResponseVO metricsPerformance(
			String batchId, String evidenceSource, String startAt, String endAt) {
		return getSuccessResponseVO(agentMessageService.callSupport(
				"metricsPerformance", metricBody(batchId, evidenceSource, startAt, endAt)));
	}

	@PostMapping("/pilotBatches/report")
	@RequireAdminPermission(AdminPermissions.ANALYTICS_EXPORT)
	public ResponseEntity<byte[]> pilotReport(String batchId, String format) {
		String normalizedFormat = StringTools.isEmpty(format)
				? "json" : format.trim().toLowerCase();
		MediaType mediaType;
		String suffix;
		switch (normalizedFormat) {
			case "json" -> {
				mediaType = MediaType.APPLICATION_JSON;
				suffix = "json";
			}
			case "csv" -> {
				mediaType = MediaType.parseMediaType("text/csv;charset=UTF-8");
				suffix = "csv";
			}
			case "markdown" -> {
				mediaType = MediaType.parseMediaType("text/markdown;charset=UTF-8");
				suffix = "md";
			}
			default -> throw new BusinessException("format 必须是 json、csv 或 markdown");
		}
		Map<String, Object> body = Map.of(
				"batchId", requireText(batchId, "batchId"),
				"format", normalizedFormat);
		byte[] report = agentMessageService.callReport("pilotReport", body);
		ContentDisposition disposition = ContentDisposition.attachment()
				.filename("pilot-report." + suffix)
				.build();
		return ResponseEntity.ok()
				.contentType(mediaType)
				.header(HttpHeaders.CONTENT_DISPOSITION, disposition.toString())
				.body(report);
	}

	private Map<String, Object> metricBody(
			String batchId, String evidenceSource, String startAt, String endAt) {
		Map<String, Object> body = new HashMap<>();
		putIfText(body, "batchId", batchId);
		putIfText(body, "evidenceSource", evidenceSource);
		putIfText(body, "startAt", startAt);
		putIfText(body, "endAt", endAt);
		return body;
	}

	private String requireText(String value, String field) {
		if (StringTools.isEmpty(value)) {
			throw new BusinessException(field + "不能为空");
		}
		return value.trim();
	}

	private Map<String, Object> supportBody(String sessionId, HttpServletRequest request) {
		Map<String, Object> body = new HashMap<>();
		body.put("sessionId", requireSessionId(sessionId));
		body.put("adminId", currentAdmin(request));
		return body;
	}

	private String currentAdmin(HttpServletRequest _request) {
		return AdminSecurityContext.requirePrincipal().getAdminId();
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
