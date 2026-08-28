package com.aishop.controller.admin;

import com.aishop.biz.AgentMessageService;
import com.aishop.entity.vo.ResponseVO;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.test.util.ReflectionTestUtils;

import java.nio.charset.StandardCharsets;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AgentMessageControllerDataAnalystTest {

    private AgentMessageController controller;
    private AgentMessageService service;

    @BeforeEach
    void setUp() {
        controller = new AgentMessageController();
        service = mock(AgentMessageService.class);
        ReflectionTestUtils.setField(controller, "agentMessageService", service);
    }

    @Test
    void askPassesPaginationContractAndPreservesAgentHttpStatus() {
        ResponseEntity<ResponseVO<Object>> upstream = structuredResponse(
                HttpStatus.FORBIDDEN, "DENY", "ANALYTICS_READ_REQUIRED");
        when(service.callDataAnalyst(eq("dataAnalyst/ask"), anyMap()))
                .thenReturn(upstream);

        ResponseEntity<ResponseVO<Object>> actual = controller.dataAnalystAsk(
                "最近七天销售额", null, 25, "tenant-a");

        assertSame(upstream, actual);
        ArgumentCaptor<Map<String, Object>> body = mapCaptor();
        verify(service).callDataAnalyst(eq("dataAnalyst/ask"), body.capture());
        assertEquals("最近七天销售额", body.getValue().get("question"));
        assertEquals(25, body.getValue().get("pageSize"));
        assertEquals("tenant-a", body.getValue().get("tenantId"));
    }

    @Test
    void pageAndClarifyUseDedicatedAgentEndpoints() {
        ResponseEntity<ResponseVO<Object>> ok = structuredResponse(
                HttpStatus.OK, "ANSWER", "SUCCEEDED");
        when(service.callDataAnalyst(eq("dataAnalyst/page"), anyMap())).thenReturn(ok);
        when(service.callDataAnalyst(eq("dataAnalyst/clarify"), anyMap())).thenReturn(ok);

        assertSame(ok, controller.dataAnalystPage("cursor-v2", 50, null));
        assertSame(ok, controller.dataAnalystClarify("token", "choice", 20, null));

        verify(service).callDataAnalyst(
                "dataAnalyst/page", Map.of("cursor", "cursor-v2", "pageSize", 50));
        verify(service).callDataAnalyst(
                "dataAnalyst/clarify",
                Map.of(
                        "clarificationToken", "token",
                        "choiceId", "choice",
                        "pageSize", 20));
    }

    @Test
    void legacyQuestionOnlyExportIsForwardedForStableResultSetError() {
        ResponseEntity<ResponseVO<Object>> badRequest = structuredResponse(
                HttpStatus.BAD_REQUEST, null, "RESULT_SET_ID_REQUIRED");
        when(service.callDataAnalyst(eq("dataAnalyst/export"), anyMap()))
                .thenReturn(badRequest);

        ResponseEntity<ResponseVO<Object>> actual = controller.dataAnalystExport(
                null, "刚才的问题", null);

        assertSame(badRequest, actual);
        verify(service).callDataAnalyst(
                "dataAnalyst/export", Map.of("question", "刚才的问题"));
    }

    @Test
    void exportDownloadPreservesStructuredNonSuccessResponse() {
        byte[] payload = "{\"code\":410}".getBytes(StandardCharsets.UTF_8);
        ResponseEntity<byte[]> upstream = ResponseEntity.status(HttpStatus.GONE)
                .body(payload);
        when(service.callDataAnalystReport(eq("dataAnalyst/export/download"), anyMap()))
                .thenReturn(upstream);

        ResponseEntity<byte[]> actual = controller.dataAnalystExportDownload("job-1", null);

        assertSame(upstream, actual);
    }

    @Test
    void exportDownloadCopiesReadOnlyUpstreamHeadersBeforeAddingDisposition() {
        byte[] payload = "{\"resultSetId\":\"result-1\"}".getBytes(StandardCharsets.UTF_8);
        ResponseEntity<byte[]> upstream = ResponseEntity.ok()
                .contentType(org.springframework.http.MediaType.APPLICATION_JSON)
                .header("X-Upstream", "preserved")
                .body(payload);
        when(service.callDataAnalystReport(eq("dataAnalyst/export/download"), anyMap()))
                .thenReturn(upstream);

        ResponseEntity<byte[]> actual = controller.dataAnalystExportDownload("job-1", null);

        assertArrayEquals(payload, actual.getBody());
        assertEquals("preserved", actual.getHeaders().getFirst("X-Upstream"));
        assertEquals("job-1.json", actual.getHeaders().getContentDisposition().getFilename());
    }

    @SuppressWarnings({"unchecked", "rawtypes"})
    private static ArgumentCaptor<Map<String, Object>> mapCaptor() {
        return ArgumentCaptor.forClass((Class) Map.class);
    }

    private static ResponseEntity<ResponseVO<Object>> structuredResponse(
            HttpStatus status, String outcome, String reasonCode) {
        ResponseVO<Object> body = new ResponseVO<>();
        body.setStatus(status.is2xxSuccessful() ? "success" : "error");
        body.setCode(status.value());
        body.setData(Map.of(
                "outcome", outcome == null ? "" : outcome,
                "reasonCode", reasonCode));
        return ResponseEntity.status(status).body(body);
    }
}
