package com.aishop.api.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotEmpty;

import java.io.Serializable;
import java.util.List;

public class SkuStockBatchChangeDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    @NotEmpty
    @Valid
    private List<SkuStockChangeDTO> items;

    public List<SkuStockChangeDTO> getItems() {
        return items;
    }

    public void setItems(List<SkuStockChangeDTO> items) {
        this.items = items;
    }
}
