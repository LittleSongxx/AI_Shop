package com.aishop.entity.po;

import lombok.Data;

import java.util.Date;

@Data
public class RefundReviewLedger {

    private Long id;
    private String refundRequestId;
    private String reviewId;
    private String action;
    private String operator;
    private String reason;
    private Date createdAt;
}
