package com.myshop.api.vo;

import java.io.Serializable;
import java.math.BigDecimal;

public class ProductInfoSnapshotVO implements Serializable {
    private static final long serialVersionUID = 1L;

    private String productId;
    private String productName;
    private Integer status;
    private String cover;
    private BigDecimal minPrice;

    public String getProductId() {
        return productId;
    }

    public void setProductId(String productId) {
        this.productId = productId;
    }

    public String getProductName() {
        return productName;
    }

    public void setProductName(String productName) {
        this.productName = productName;
    }

    public Integer getStatus() {
        return status;
    }

    public void setStatus(Integer status) {
        this.status = status;
    }

    public String getCover() {
        return cover;
    }

    public void setCover(String cover) {
        this.cover = cover;
    }

    public BigDecimal getMinPrice() {
        return minPrice;
    }

    public void setMinPrice(BigDecimal minPrice) {
        this.minPrice = minPrice;
    }
}
