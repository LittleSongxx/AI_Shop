package com.aishop.api.dto;

import java.io.Serializable;

public class ImageUploadResultDTO implements Serializable {

    private String path;

    private Boolean pendingReview;

    private Integer moderationId;

    private String moderationStatus;

    private String scene;

    private String assetId;

    private String contentSha256;

    private String mimeType;

    private Integer width;

    private Integer height;

    private String expiresAt;

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

    public String getAssetId() {
        return assetId;
    }

    public void setAssetId(String assetId) {
        this.assetId = assetId;
    }

    public String getContentSha256() {
        return contentSha256;
    }

    public void setContentSha256(String contentSha256) {
        this.contentSha256 = contentSha256;
    }

    public String getMimeType() {
        return mimeType;
    }

    public void setMimeType(String mimeType) {
        this.mimeType = mimeType;
    }

    public Integer getWidth() {
        return width;
    }

    public void setWidth(Integer width) {
        this.width = width;
    }

    public Integer getHeight() {
        return height;
    }

    public void setHeight(Integer height) {
        this.height = height;
    }

    public String getExpiresAt() {
        return expiresAt;
    }

    public void setExpiresAt(String expiresAt) {
        this.expiresAt = expiresAt;
    }
}
