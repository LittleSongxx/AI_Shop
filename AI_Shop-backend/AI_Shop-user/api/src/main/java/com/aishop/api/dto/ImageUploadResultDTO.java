package com.aishop.api.dto;

import java.io.Serializable;

public class ImageUploadResultDTO implements Serializable {

    private String path;

    private Boolean pendingReview;

    private Integer moderationId;

    private String moderationStatus;

    private String scene;

    public ImageUploadResultDTO() {
    }

    public ImageUploadResultDTO(String path, Boolean pendingReview) {
        this.path = path;
        this.pendingReview = pendingReview;
    }

    public String getPath() {
        return path;
    }

    public void setPath(String path) {
        this.path = path;
    }

    public Boolean getPendingReview() {
        return pendingReview;
    }

    public void setPendingReview(Boolean pendingReview) {
        this.pendingReview = pendingReview;
    }

    public Integer getModerationId() {
        return moderationId;
    }

    public void setModerationId(Integer moderationId) {
        this.moderationId = moderationId;
    }

    public String getModerationStatus() {
        return moderationStatus;
    }

    public void setModerationStatus(String moderationStatus) {
        this.moderationStatus = moderationStatus;
    }

    public String getScene() {
        return scene;
    }

    public void setScene(String scene) {
        this.scene = scene;
    }
}
