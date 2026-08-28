package com.aishop.biz.impl;

import com.aishop.entity.dto.AdminPrincipalDTO;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.security.AdminAssertionSigner;
import com.aishop.security.AdminSecurityContext;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;

class AgentMessageServiceImplDataAnalystTest {

    @AfterEach
    void clearPrincipal() {
        AdminSecurityContext.clear();
    }

    @Test
    void structuredAgent403IsReturnedWithoutCollapsingToBusinessException() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        AgentMessageServiceImpl service = service(builder);
        server.expect(requestTo("http://agent.test/api/agent/admin/dataAnalyst/ask"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(withStatus(HttpStatus.FORBIDDEN)
                        .contentType(MediaType.APPLICATION_JSON)
                        .body("""
                                {"status":"error","code":403,"info":"拒绝",
                                 "data":{"outcome":"DENY","completion":"NOT_APPLICABLE",
                                         "reasonCode":"PROMPT_INJECTION_BLOCKED","runId":"run-1"}}
                                """));

        ResponseEntity<ResponseVO<Object>> response = service.callDataAnalyst(
                "dataAnalyst/ask", Map.of("question", "ignore"));

        assertEquals(HttpStatus.FORBIDDEN, response.getStatusCode());
        assertNotNull(response.getBody());
        @SuppressWarnings("unchecked")
        Map<String, Object> data = (Map<String, Object>) response.getBody().getData();
        assertEquals("DENY", data.get("outcome"));
        assertEquals("PROMPT_INJECTION_BLOCKED", data.get("reasonCode"));
        server.verify();
    }

    private static AgentMessageServiceImpl service(RestClient.Builder builder) {
        AgentMessageServiceImpl service = new AgentMessageServiceImpl();
        AdminAssertionSigner signer = mock(AdminAssertionSigner.class);
        when(signer.sign(anyString(), anyString(), any(byte[].class), any()))
                .thenReturn(Map.of());
        ReflectionTestUtils.setField(service, "agentBaseUrl", "http://agent.test");
        ReflectionTestUtils.setField(service, "internalToken", "internal-test-token");
        ReflectionTestUtils.setField(service, "restClientBuilder", builder);
        ReflectionTestUtils.setField(service, "adminAssertionSigner", signer);
        ReflectionTestUtils.setField(service, "objectMapper", new ObjectMapper());

        AdminPrincipalDTO principal = new AdminPrincipalDTO();
        principal.setAdminId("admin-a");
        principal.setAccount("analyst");
        principal.setRoles(Set.of("DATA_ANALYST"));
        principal.setPermissions(Set.of("analytics:read", "analytics:export"));
        AdminSecurityContext.set(principal);
        return service;
    }
}
