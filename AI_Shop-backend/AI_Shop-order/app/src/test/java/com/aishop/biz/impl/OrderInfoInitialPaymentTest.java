package com.aishop.biz.impl;

import com.aishop.api.dto.PayInfoDTO;
import com.aishop.api.support.PayFeignSupport;
import com.aishop.exception.BusinessException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OrderInfoInitialPaymentTest {

    @Mock
    private PayFeignSupport payFeignSupport;

    private OrderInfoServiceImpl service;

    @BeforeEach
    void setUp() {
        service = new OrderInfoServiceImpl();
        ReflectionTestUtils.setField(service, "payFeignSupport", payFeignSupport);
    }

    @Test
    void successfulPaymentFormIsReturnedUnchanged() {
        BigDecimal amount = new BigDecimal("19.90");
        PayInfoDTO expected = new PayInfoDTO("<form>pay</form>", "pay-1", amount);
        when(payFeignSupport.getPayUrl("alipay_wap", "pay-1", "subject", amount))
                .thenReturn(expected);

        PayInfoDTO actual = service.requestInitialPayInfoBestEffort(
                "alipay_wap", "pay-1", "subject", amount);

        assertSame(expected, actual);
    }

    @Test
    void unavailablePaymentFormKeepsPayableOrderIdentity() {
        BigDecimal amount = new BigDecimal("19.90");
        when(payFeignSupport.getPayUrl("alipay_wap", "pay-2", "subject", amount))
                .thenThrow(new BusinessException("provider unavailable"));

        PayInfoDTO actual = service.requestInitialPayInfoBestEffort(
                "alipay_wap", "pay-2", "subject", amount);

        assertNull(actual.getPayInfo());
        assertEquals("pay-2", actual.getPayOrderId());
        assertEquals(amount, actual.getAmount());
    }
}
