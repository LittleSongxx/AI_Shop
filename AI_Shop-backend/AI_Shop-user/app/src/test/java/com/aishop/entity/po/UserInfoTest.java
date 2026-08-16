package com.aishop.entity.po;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class UserInfoTest {

    @Test
    void toStringNeverIncludesPassword() {
        UserInfo userInfo = new UserInfo();
        userInfo.setUserId("user-1");
        userInfo.setEmail("user@example.com");
        userInfo.setPassword("super-secret-password");

        String rendered = userInfo.toString();

        assertTrue(rendered.contains("user-1"));
        assertTrue(rendered.contains("user@example.com"));
        assertFalse(rendered.contains("super-secret-password"));
        assertFalse(rendered.contains("密码"));
    }
}
