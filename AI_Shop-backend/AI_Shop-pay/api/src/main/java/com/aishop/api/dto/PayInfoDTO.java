package com.aishop.api.dto;

import java.math.BigDecimal;

public class PayInfoDTO implements IdempotencyReplayAware {
    private String payInfo;
    private String payOrderId;

    private String orderId;
    private BigDecimal amount;
    private Boolean idempotencyReplayed;

    public PayInfoDTO(String payInfo, String payOrderId, BigDecimal amount) {
        this.payInfo = payInfo;
        this.payOrderId = payOrderId;
        this.amount = amount;
    }

    public PayInfoDTO() {
    }

    public String getPayInfo() {
        return payInfo;
    }

    public void setPayInfo(String payInfo) {
        this.payInfo = payInfo;
    }

    public String getPayOrderId() {
        return payOrderId;
    }

    public void setPayOrderId(String payOrderId) {
        this.payOrderId = payOrderId;
    }

    public String getOrderId() {
        return orderId;
    }

    public void setOrderId(String orderId) {
        this.orderId = orderId;
    }

    public BigDecimal getAmount() {
        return amount;
    }

    public void setAmount(BigDecimal amount) {
        this.amount = amount;
    }

    @Override
    public Boolean getIdempotencyReplayed() {
        return idempotencyReplayed;
    }

    @Override
    public void setIdempotencyReplayed(Boolean idempotencyReplayed) {
        this.idempotencyReplayed = idempotencyReplayed;
    }
}
