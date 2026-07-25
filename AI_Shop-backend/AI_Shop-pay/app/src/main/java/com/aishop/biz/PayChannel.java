package com.aishop.biz;

import com.aishop.api.dto.PayInfoDTO;
import com.aishop.api.dto.PayOrderNotifyDTO;
import com.aishop.api.enums.PayChannelEnum;

import java.math.BigDecimal;
import java.util.Map;

public interface PayChannel {

    PayInfoDTO getPayUrl(PayChannelEnum payChannelEnum, String payOrderId, String subject, BigDecimal amount);

    PayOrderNotifyDTO payNotify(Map<String, String> requestParams, String jsonBody);

    PayOrderNotifyDTO queryOrder(String payOrderId);

    void refund(String sourcePayOrderId, String payOrderId, BigDecimal refundAmount);

    void closeOrder(String payOrderId);
}
