package com.aishop.compensation;

import com.aishop.entity.po.ProductItem;

import java.util.List;

public interface StockBatchCompensatePort {

    int changeStockBatch(List<ProductItem> items);
}
