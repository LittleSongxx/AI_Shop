package com.aishop.biz.impl;

import com.aishop.api.enums.PayChannelEnum;
import com.aishop.entity.config.AppConfig;
import com.aishop.exception.BusinessException;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class PayChannel4AliPayTest {

    @Test
    void missingCredentialsFailBeforeCallingTheExternalSdk() {
        AppConfig appConfig = mock(AppConfig.class);
        when(appConfig.isAlipayConfigured()).thenReturn(false);
        PayChannel4AliPay channel = new PayChannel4AliPay();
        ReflectionTestUtils.setField(channel, "appConfig", appConfig);

        BusinessException error = assertThrows(
                BusinessException.class,
                () -> channel.getPayUrl(
                        PayChannelEnum.ALIPAY_WAP,
                        "pay-1",
                        "test order",
                        BigDecimal.ONE
                )
        );

        assertEquals("支付宝支付未配置", error.getMessage());
    }
}
