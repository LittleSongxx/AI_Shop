package com.aishop.entity.po;

import com.aishop.constants.Constants;
import com.aishop.entity.dto.RecommendationAttributionCarrier;
import com.aishop.utils.StringTools;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import org.springframework.validation.annotation.Validated;

import java.util.Date;

@Validated
public class ProductItem implements RecommendationAttributionCarrier {
    @NotEmpty
    private String productId;
    @NotEmpty
    private String propertyValueIds;
    @NotNull
    @Min(1)
    @Max(Constants.ORDER_MAX_BUY_COUNT_PER_SKU)
    private Integer buyCount;


    public String getProductId() {
        return productId;
    }

    public void setProductId(String productId) {
        this.productId = productId;
    }

    public String getPropertyValueIds() {
        return propertyValueIds;
    }

    public void setPropertyValueIds(String propertyValueIds) {
        this.propertyValueIds = propertyValueIds;
        if (!StringTools.isEmpty(propertyValueIds)) {
            this.propertyValueIdHash = StringTools.encodeByMD5(propertyValueIds);
        }
    }

    public Integer getBuyCount() {
        return buyCount;
    }

    public void setBuyCount(Integer buyCount) {
        this.buyCount = buyCount;
    }

    public String getRemark() {
        return remark;
    }

    public void setRemark(String remark) {
        this.remark = remark;
    }

    private String remark;

    private String propertyValueIdHash;

    private String aiRequestId;

    private Integer aiPosition;

    private String aiSource;

    private Date aiAttributedAt;

    public String getPropertyValueIdHash() {
        return propertyValueIdHash;
    }

    public void setPropertyValueIdHash(String propertyValueIdHash) {
        this.propertyValueIdHash = propertyValueIdHash;
    }

    public String getAiRequestId() {
        return aiRequestId;
    }

    public void setAiRequestId(String aiRequestId) {
        this.aiRequestId = aiRequestId;
    }

    public Integer getAiPosition() {
        return aiPosition;
    }

    public void setAiPosition(Integer aiPosition) {
        this.aiPosition = aiPosition;
    }

    public String getAiSource() {
        return aiSource;
    }

    public void setAiSource(String aiSource) {
        this.aiSource = aiSource;
    }

    public Date getAiAttributedAt() {
        return aiAttributedAt;
    }

    public void setAiAttributedAt(Date aiAttributedAt) {
        this.aiAttributedAt = aiAttributedAt;
    }
}
