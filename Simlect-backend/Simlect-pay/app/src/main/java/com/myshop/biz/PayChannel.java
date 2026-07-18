package com.myshop.biz;

import com.myshop.entity.dto.PayInfoDTO;
import com.myshop.entity.dto.PayOrderNotifyDTO;
import com.myshop.entity.enums.PayChannelEnum;

import java.math.BigDecimal;
import java.util.Map;

public interface PayChannel {

    PayInfoDTO getPayUrl(PayChannelEnum payChannelEnum, String payOrderId, String subject, BigDecimal amount);

    PayOrderNotifyDTO payNotify(Map<String, String> requestParams, String jsonBody);

    PayOrderNotifyDTO queryOrder(String payOrderId);

    void refund(String sourcePayOrderId, String payOrderId, BigDecimal refundAmount);

    void closeOrder(String payOrderId);
}
