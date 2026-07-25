package com.aishop.entity.po;

import lombok.Data;

import java.math.BigDecimal;
import java.util.Date;

@Data
public class RefundRequest {

    private String refundRequestId;
    private String refundOrderNo;
    private String sourcePayOrderId;
    private String orderId;
    private String orderItemId;
    private String userId;
    private String productId;
    private String propertyValueIdHash;
    private Integer buyCount;
    private BigDecimal refundAmount;
    private String payChannel;
    private String status;
    private Integer retryCount;
    private Date nextRetryTime;
    private String lastError;
    private Date createdAt;
    private Date updatedAt;
    private Date paymentConfirmedAt;
    private Date completedAt;
}
