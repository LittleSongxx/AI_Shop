package com.aishop.entity.po;

import java.util.Date;

public class OrderRequestIdempotency {

    private Long id;
    private String userId;
    private String commandType;
    private String idempotencyKey;
    private String requestHash;
    private String status;
    private String responseJson;
    private Integer reconcileAttempts;
    private Date reconcileDeadline;
    private Date lastReconcileAt;
    private String reviewReason;
    private Date createTime;
    private Date updateTime;

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getUserId() {
        return userId;
    }

    public void setUserId(String userId) {
        this.userId = userId;
    }

    public String getCommandType() {
        return commandType;
    }

    public void setCommandType(String commandType) {
        this.commandType = commandType;
    }

    public String getIdempotencyKey() {
        return idempotencyKey;
    }

    public void setIdempotencyKey(String idempotencyKey) {
        this.idempotencyKey = idempotencyKey;
    }

    public String getRequestHash() {
        return requestHash;
    }

    public void setRequestHash(String requestHash) {
        this.requestHash = requestHash;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public String getResponseJson() {
        return responseJson;
    }

    public void setResponseJson(String responseJson) {
        this.responseJson = responseJson;
    }

    public Integer getReconcileAttempts() {
        return reconcileAttempts;
    }

    public void setReconcileAttempts(Integer reconcileAttempts) {
        this.reconcileAttempts = reconcileAttempts;
    }

    public Date getReconcileDeadline() {
        return reconcileDeadline;
    }

    public void setReconcileDeadline(Date reconcileDeadline) {
        this.reconcileDeadline = reconcileDeadline;
    }

    public Date getLastReconcileAt() {
        return lastReconcileAt;
    }

    public void setLastReconcileAt(Date lastReconcileAt) {
        this.lastReconcileAt = lastReconcileAt;
    }

    public String getReviewReason() {
        return reviewReason;
    }

    public void setReviewReason(String reviewReason) {
        this.reviewReason = reviewReason;
    }

    public Date getCreateTime() {
        return createTime;
    }

    public void setCreateTime(Date createTime) {
        this.createTime = createTime;
    }

    public Date getUpdateTime() {
        return updateTime;
    }

    public void setUpdateTime(Date updateTime) {
        this.updateTime = updateTime;
    }
}
