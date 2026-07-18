package com.myshop.compensation;

import com.myshop.entity.po.ProductItem;

import java.util.List;

public interface StockBatchCompensatePort {

    int changeStockBatch(List<ProductItem> items);
}
