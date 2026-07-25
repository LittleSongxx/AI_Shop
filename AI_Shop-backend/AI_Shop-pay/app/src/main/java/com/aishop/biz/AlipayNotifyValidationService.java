package com.aishop.biz;

import com.aishop.api.enums.PayChannelEnum;
import com.aishop.entity.config.AppConfig;
import com.aishop.entity.po.PayTradeRecord;
import com.aishop.exception.BusinessException;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Map;

/**
 * Validates a signed notification against the payment intent stored locally.
 * Signature verification proves origin; this check proves that the notification
 * belongs to an order and amount that this application actually created.
 */
@Service
public class AlipayNotifyValidationService {

    private static final String TRADE_SUCCESS = "TRADE_SUCCESS";
    private static final String TRADE_FINISHED = "TRADE_FINISHED";

    @Resource
    private AppConfig appConfig;
    @Resource
    private PayTradeRecordService payTradeRecordService;

    public PayTradeRecord validate(Map<String, String> params) {
        if (params == null) {
            throw new BusinessException("支付宝回调参数无效");
        }
        String payOrderId = required(params, "out_trade_no");
        String channelOrderId = required(params, "trade_no");
        String status = required(params, "trade_status");
        if (!TRADE_SUCCESS.equals(status) && !TRADE_FINISHED.equals(status)) {
            throw new BusinessException("支付宝交易状态无效");
        }

        PayTradeRecord record = payTradeRecordService.findByPayOrderId(payOrderId);
        if (record == null) {
            throw new BusinessException("支付订单不存在");
        }
        if (!payOrderId.equals(record.getPayOrderId())) {
            throw new BusinessException("支付订单号不匹配");
        }
        PayChannelEnum storedChannel = PayChannelEnum.resolve(record.getPayChannel());
        if (storedChannel == null || !"alipay".equals(storedChannel.getPayChannel())) {
            throw new BusinessException("支付渠道无效");
        }
        if (!StringTools.isEmpty(record.getChannelOrderId())
                && !record.getChannelOrderId().equals(channelOrderId)) {
            throw new BusinessException("支付渠道订单号不匹配");
        }
        if (!equalsText(appConfig.getAlipayAppid(), params.get("app_id"))) {
            throw new BusinessException("支付宝应用不匹配");
        }
        if (!equalsText(appConfig.getAlipaySellerId(), params.get("seller_id"))) {
            throw new BusinessException("支付宝卖家不匹配");
        }
        BigDecimal notifiedAmount = parseAmount(params.get("total_amount"));
        if (record.getPayAmount() == null
                || record.getPayAmount().compareTo(notifiedAmount) != 0) {
            throw new BusinessException("支付金额不匹配");
        }
        return record;
    }

    private String required(Map<String, String> params, String key) {
        String value = params.get(key);
        if (StringTools.isEmpty(value)) {
            throw new BusinessException("支付宝回调缺少" + key);
        }
        return value;
    }

    private boolean equalsText(String expected, String actual) {
        return !StringTools.isEmpty(expected) && expected.equals(actual);
    }

    private BigDecimal parseAmount(String value) {
        if (StringTools.isEmpty(value)) {
            throw new BusinessException("支付金额无效");
        }
        if (!value.matches("(?:0|[1-9]\\d*)(?:\\.\\d{1,2})?")) {
            throw new BusinessException("支付金额无效");
        }
        try {
            BigDecimal amount = new BigDecimal(value);
            if (amount.signum() < 0 || amount.scale() > 2) {
                throw new BusinessException("支付金额无效");
            }
            return amount.setScale(2, RoundingMode.UNNECESSARY);
        } catch (NumberFormatException | ArithmeticException e) {
            throw new BusinessException("支付金额无效");
        }
    }
}
