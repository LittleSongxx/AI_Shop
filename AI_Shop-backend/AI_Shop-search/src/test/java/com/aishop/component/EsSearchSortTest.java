package com.aishop.component;

import com.aishop.entity.enums.ProductSortKey;
import com.aishop.entity.enums.SortDirection;
import org.junit.jupiter.api.Test;
import org.springframework.data.domain.Sort;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;

class EsSearchSortTest {

    @Test
    void onlyTheSharedSortKeysProduceKnownIndexFields() {
        assertEquals(
                List.of("minPrice", "totalSale", "productId"),
                EsSearchComponent.buildSort(ProductSortKey.PRICE, SortDirection.ASC)
                        .stream().map(Sort.Order::getProperty).toList());
        assertEquals(
                List.of("totalSale", "minPrice", "productId"),
                EsSearchComponent.buildSort(ProductSortKey.SALE, SortDirection.DESC)
                        .stream().map(Sort.Order::getProperty).toList());
        assertEquals(
                List.of("_score", "totalSale", "productId"),
                EsSearchComponent.buildSort(null, null)
                        .stream().map(Sort.Order::getProperty).toList());
    }
}
