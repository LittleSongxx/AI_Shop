package com.aishop.api.dto;

import java.io.Serializable;

public class AgentImageAssetRequestDTO implements Serializable {

    private String userId;
    private String imageAssetId;

    public String getUserId() {
        return userId;
    }

    public void setUserId(String userId) {
        this.userId = userId;
    }

    public String getImageAssetId() {
        return imageAssetId;
    }

    public void setImageAssetId(String imageAssetId) {
        this.imageAssetId = imageAssetId;
    }
}
