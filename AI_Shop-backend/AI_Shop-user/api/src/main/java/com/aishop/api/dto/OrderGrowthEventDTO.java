package com.aishop.api.dto;

import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;

import java.io.Serializable;
import java.math.BigDecimal;

public class OrderGrowthEventDTO implements Serializable {
    private static final long serialVersionUID = 1L;

    @NotEmpty
    private String orderId;
    @NotEmpty
    private String userId;
    @NotNull
    private BigDecimal payAmount;

    public OrderGrowthEventDTO() {
    }

    public OrderGrowthEventDTO(String orderId, String userId, BigDecimal payAmount) {
        this.orderId = orderId;
        this.userId = userId;
        this.payAmount = payAmount;
    }

    public String getOrderId() {
        return orderId;
    }

    public void setOrderId(String orderId) {
        this.orderId = orderId;
    }

    public String getUserId() {
        return userId;
    }

    public void setUserId(String userId) {
        this.userId = userId;
    }

    public BigDecimal getPayAmount() {
        return payAmount;
    }

    public void setPayAmount(BigDecimal payAmount) {
        this.payAmount = payAmount;
    }
}
