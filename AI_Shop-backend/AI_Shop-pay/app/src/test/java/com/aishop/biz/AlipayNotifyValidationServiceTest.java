package com.aishop.biz;

import com.aishop.entity.config.AppConfig;
import com.aishop.entity.po.PayTradeRecord;
import com.aishop.exception.BusinessException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AlipayNotifyValidationServiceTest {

    @Mock
    private AppConfig appConfig;
    @Mock
    private PayTradeRecordService payTradeRecordService;
    @InjectMocks
    private AlipayNotifyValidationService service;

    private PayTradeRecord record;

    @BeforeEach
    void setUp() {
        when(appConfig.getAlipayAppid()).thenReturn("app-1");
        when(appConfig.getAlipaySellerId()).thenReturn("seller-1");

        record = new PayTradeRecord();
        record.setPayOrderId("pay-1");
        record.setPayAmount(new BigDecimal("99.00"));
        record.setPayChannel("alipay_pc");
        when(payTradeRecordService.findByPayOrderId(anyString())).thenReturn(record);
    }

    @Test
    void acceptsNotificationThatMatchesStoredPaymentIntent() {
        assertDoesNotThrow(() -> service.validate(validParams()));
    }

    @Test
    void rejectsForgedOrderAmountApplicationSellerStatusAndChannelOrder() {
        Map<String, String> params = validParams();

        params.put("out_trade_no", "other-order");
        assertRejected(params);

        params = validParams();
        params.put("total_amount", "98.00");
        assertRejected(params);

        params = validParams();
        params.put("app_id", "other-app");
        assertRejected(params);

        params = validParams();
        params.put("seller_id", "other-seller");
        assertRejected(params);

        params = validParams();
        params.put("trade_status", "WAIT_BUYER_PAY");
        assertRejected(params);

        record.setChannelOrderId("trade-1");
        params = validParams();
        params.put("trade_no", "trade-2");
        assertRejected(params);
    }

    @Test
    void rejectsMalformedAmountAndNonAlipayStoredChannel() {
        Map<String, String> params = validParams();
        params.put("total_amount", "99.001");
        assertRejected(params);

        record.setPayChannel("unknown");
        assertRejected(validParams());
    }

    private void assertRejected(Map<String, String> params) {
        assertThrows(BusinessException.class, () -> service.validate(params));
    }

    private Map<String, String> validParams() {
        Map<String, String> params = new HashMap<>();
        params.put("out_trade_no", "pay-1");
        params.put("trade_no", "trade-1");
        params.put("trade_status", "TRADE_SUCCESS");
        params.put("app_id", "app-1");
        params.put("seller_id", "seller-1");
        params.put("total_amount", "99.00");
        return params;
    }
}
