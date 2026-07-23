package com.simlect.entity.dto;

public class RagDataDTO {
    private String dataId;
    private String type;
    private Long version;

    public RagDataDTO(String dataId, String type) {
        this(dataId, type, System.currentTimeMillis());
    }

    public RagDataDTO(String dataId, String type, Long version) {
        this.dataId = dataId;
        this.type = type;
        this.version = version;
    }
    public RagDataDTO() {
    }

    public String getDataId() {
        return dataId;
    }

    public void setDataId(String dataId) {
        this.dataId = dataId;
    }

    public String getType() {
        return type;
    }

    public void setType(String type) {
        this.type = type;
    }

    public Long getVersion() {
        return version;
    }

    public void setVersion(Long version) {
        this.version = version;
    }
}
