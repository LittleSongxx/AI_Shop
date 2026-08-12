package com.aishop.controller;

import com.aishop.biz.UserInfoService;
import com.aishop.entity.dto.PrivacyConfirmRequest;
import com.aishop.entity.dto.TokenUserInfoDTO;
import com.aishop.entity.po.UserInfo;
import com.aishop.exception.BusinessException;
import com.aishop.integration.PrivacyAgentClient;
import com.aishop.service.PasswordService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class UserPrivacyControllerTest {

    private PrivacyAgentClient client;
    private UserInfoService userInfoService;
    private PasswordService passwordService;
    private TestController controller;

    @BeforeEach
    void setUp() {
        client = mock(PrivacyAgentClient.class);
        userInfoService = mock(UserInfoService.class);
        passwordService = mock(PasswordService.class);
        controller = new TestController(client, userInfoService, passwordService, "user-1");
    }

    @Test
    void createDeletionUsesCurrentSessionIdentityAndPasswordConfirmation() {
        UserInfo user = new UserInfo();
        user.setUserId("user-1");
        user.setPassword("stored-hash");
        when(userInfoService.getUserInfoByUserId("user-1")).thenReturn(user);
        when(passwordService.matches("raw-password", "stored-hash")).thenReturn(true);
        when(client.createJob("user-1", "DELETE", "idem-1"))
                .thenReturn(Map.of("jobId", "privacy-1"));

        Object result = controller.createDeletion(
                "idem-1", new PrivacyConfirmRequest("raw-password")).getData();

        assertEquals(Map.of("jobId", "privacy-1"), result);
        verify(client).createJob("user-1", "DELETE", "idem-1");
    }

    @Test
    void wrongPasswordNeverStartsPrivacyJob() {
        UserInfo user = new UserInfo();
        user.setPassword("stored-hash");
        when(userInfoService.getUserInfoByUserId("user-1")).thenReturn(user);
        when(passwordService.matches("wrong", "stored-hash")).thenReturn(false);

        BusinessException exception = assertThrows(
                BusinessException.class,
                () -> controller.createExport("idem-1", new PrivacyConfirmRequest("wrong")));

        assertEquals(403, exception.getCode());
        verify(client, never()).createJob("user-1", "EXPORT", "idem-1");
    }

    @Test
    void jobLookupCannotAcceptAClientSuppliedTargetUser() {
        when(client.getJob("user-1", "privacy-1"))
                .thenReturn(Map.of("jobId", "privacy-1"));

        Object result = controller.getJob("privacy-1").getData();

        assertEquals(Map.of("jobId", "privacy-1"), result);
        verify(client).getJob("user-1", "privacy-1");
    }

    private static final class TestController extends UserPrivacyController {
        private final TokenUserInfoDTO principal;

        private TestController(
                PrivacyAgentClient client,
                UserInfoService userInfoService,
                PasswordService passwordService,
                String userId) {
            super(client, userInfoService, passwordService);
            principal = new TokenUserInfoDTO();
            principal.setUserId(userId);
        }

        @Override
        public TokenUserInfoDTO getTokenUserInfo() {
            return principal;
        }
    }
}
