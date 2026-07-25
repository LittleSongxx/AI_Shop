package com.aishop.support;

import com.aishop.utils.StringTools;

public final class MqIdempotencyKeys {

    private MqIdempotencyKeys() {
    }

    public static String ragProduct(String productId, long version) {
        return "rag:product:" + require(productId) + ":v:" + version;
    }

    public static String ragFaq(String questionId, long version) {
        return "rag:faq:" + require(questionId) + ":v:" + version;
    }

    public static String payTimeout(String orderId) {
        return "pay:timeout:" + require(orderId);
    }

    public static String payLogistics(String orderId) {
        return payLogistics(orderId, 0);
    }

    public static String payLogistics(String orderId, int step) {
        return "pay:logistics:" + require(orderId) + ":step:" + step;
    }

    public static String payConfirm(String orderId) {
        return "pay:confirm:" + require(orderId);
    }

    public static String refundStock(String refundRequestId, int attempt) {
        return "refund:stock:" + require(refundRequestId) + ":attempt:" + attempt;
    }

    public static String refundResult(String refundRequestId) {
        return "refund:result:" + require(refundRequestId);
    }

    public static String browseRecord(String userId, String productId) {
        return "browse:" + require(userId) + ":" + require(productId);
    }

    public static String signRecord(String userId, String yyyyMMdd) {
        return "sign:record:" + require(userId) + ":" + require(yyyyMMdd);
    }

    public static String notification(String userId, String bizType, String bizId) {
        return "notify:" + require(userId) + ":"
                + (bizType == null ? "" : bizType) + ":"
                + (bizId == null ? "" : bizId);
    }

    public static String tempBanUnban(String userId, long unbanAtMs) {
        return "tempban:unban:" + require(userId) + ":" + unbanAtMs;
    }

    private static String require(String value) {
        if (StringTools.isEmpty(value)) {
            throw new IllegalArgumentException("MQ 幂等键参数不能为空");
        }
        return value;
    }
}
