package com.aishop.api.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;

import java.io.Serializable;
import java.util.List;

/**
 * Idempotent stock restoration for every SKU belonging to one closed payment aggregate.
 */
public class OrderStockRestoreDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    @NotBlank
    private String payOrderId;
    @NotEmpty
    @Valid
    private List<SkuStockChangeDTO> items;

    public String getPayOrderId() {
        return payOrderId;
    }

    public void setPayOrderId(String payOrderId) {
        this.payOrderId = payOrderId;
    }

    public List<SkuStockChangeDTO> getItems() {
        return items;
    }

    public void setItems(List<SkuStockChangeDTO> items) {
        this.items = items;
    }
}
