package com.aishop.api.vo;

import java.io.Serializable;
import java.util.ArrayList;
import java.util.List;

public class ProductRagIndexVO implements Serializable {
    private static final long serialVersionUID = 1L;

    private String productId;
    private String productName;
    private String productDesc;
    private String categoryId;
    private String parentCategoryId;
    private String brand;
    private Integer status;
    private List<ProductRagPropertyVO> propertyValues = new ArrayList<>();
    private List<ProductRagSkuVO> skus = new ArrayList<>();

    public String getProductId() { return productId; }
    public void setProductId(String productId) { this.productId = productId; }
    public String getProductName() { return productName; }
    public void setProductName(String productName) { this.productName = productName; }
    public String getProductDesc() { return productDesc; }
    public void setProductDesc(String productDesc) { this.productDesc = productDesc; }
    public String getCategoryId() { return categoryId; }
    public void setCategoryId(String categoryId) { this.categoryId = categoryId; }
    public String getParentCategoryId() { return parentCategoryId; }
    public void setParentCategoryId(String parentCategoryId) { this.parentCategoryId = parentCategoryId; }
    public String getBrand() { return brand; }
    public void setBrand(String brand) { this.brand = brand; }
    public Integer getStatus() { return status; }
    public void setStatus(Integer status) { this.status = status; }
    public List<ProductRagPropertyVO> getPropertyValues() { return propertyValues; }
    public void setPropertyValues(List<ProductRagPropertyVO> propertyValues) { this.propertyValues = propertyValues; }
    public List<ProductRagSkuVO> getSkus() { return skus; }
    public void setSkus(List<ProductRagSkuVO> skus) { this.skus = skus; }
}
