package com.simlect.api.dto;

import java.io.Serializable;

public class RefundStockResultDTO implements Serializable {

    private String refundRequestId;
    private String businessKey;

    public RefundStockResultDTO() {
    }

    public RefundStockResultDTO(String refundRequestId, String businessKey) {
        this.refundRequestId = refundRequestId;
        this.businessKey = businessKey;
    }

    public String getRefundRequestId() {
        return refundRequestId;
    }

    public void setRefundRequestId(String refundRequestId) {
        this.refundRequestId = refundRequestId;
    }

    public String getBusinessKey() {
        return businessKey;
    }

    public void setBusinessKey(String businessKey) {
        this.businessKey = businessKey;
    }
}
