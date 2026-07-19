package com.simlect.api.support;



import com.simlect.api.ProductFeignClient;

import com.simlect.api.dto.LessStockPageDTO;
import com.simlect.api.dto.ProductIdDTO;
import com.simlect.api.dto.ProductIdListDTO;
import com.simlect.api.dto.ProductSalesIncreaseDTO;
import com.simlect.api.dto.ProductSnapshotBatchVO;
import com.simlect.api.vo.ProductInfoSnapshotVO;
import com.simlect.api.vo.ProductPropertyValueSnapshotVO;
import com.simlect.api.vo.ProductRagIndexVO;
import com.simlect.api.vo.ProductSearchIndexVO;
import com.simlect.api.vo.ProductSkuSnapshotVO;
import com.simlect.entity.vo.PaginationResultVO;
import com.simlect.api.vo.ProductSkuListVO;
import jakarta.annotation.Resource;

import org.springframework.stereotype.Component;



import java.util.Collections;

import java.util.HashMap;

import java.util.LinkedHashMap;

import java.util.List;

import java.util.Map;

import java.util.function.Function;

import java.util.stream.Collectors;



@Component

public class ProductFeignSupport {



    @Resource

    private ProductFeignClient productFeignClient;

    @Resource

    private FeignResponseSupport feignResponseSupport;



    public ProductSnapshotBatchVO snapshotBatch(List<String> productIds) {

        if (productIds == null || productIds.isEmpty()) {

            ProductSnapshotBatchVO empty = new ProductSnapshotBatchVO();

            empty.setProducts(Collections.emptyList());

            empty.setSkus(Collections.emptyList());

            empty.setPropertyValues(Collections.emptyList());

            return empty;

        }

        return feignResponseSupport.call(

                () -> productFeignClient.snapshotBatch(new ProductIdListDTO(productIds)),

                "查询商品快照失败");

    }



    public Map<String, ProductInfoSnapshotVO> toProductInfoMap(ProductSnapshotBatchVO batch) {

        if (batch == null || batch.getProducts() == null) {

            return new HashMap<>();

        }

        return batch.getProducts().stream()

                .collect(Collectors.toMap(ProductInfoSnapshotVO::getProductId, Function.identity(), (a, b) -> b));

    }



    public Map<String, ProductPropertyValueSnapshotVO> toPropertyValueMap(ProductSnapshotBatchVO batch) {

        if (batch == null || batch.getPropertyValues() == null) {

            return new HashMap<>();

        }

        return batch.getPropertyValues().stream()

                .collect(Collectors.toMap(

                        item -> item.getProductId() + item.getPropertyValueId(),

                        Function.identity(),

                        (a, b) -> b));

    }



    public Map<String, ProductSkuSnapshotVO> toSkuMapByPropertyValueIds(ProductSnapshotBatchVO batch) {

        if (batch == null || batch.getSkus() == null) {

            return new HashMap<>();

        }

        return batch.getSkus().stream()

                .collect(Collectors.toMap(

                        item -> item.getProductId() + nullToEmpty(item.getPropertyValueIds()),

                        Function.identity(),

                        (a, b) -> b));

    }



    public Map<String, ProductSkuSnapshotVO> toDefaultSkuByProductId(ProductSnapshotBatchVO batch) {

        Map<String, ProductSkuSnapshotVO> map = new LinkedHashMap<>();

        if (batch == null || batch.getSkus() == null) {

            return map;

        }

        for (ProductSkuSnapshotVO sku : batch.getSkus()) {

            map.putIfAbsent(sku.getProductId(), sku);

        }

        return map;

    }



    public ProductSkuSnapshotVO defaultSku(String productId) {

        return feignResponseSupport.call(

                () -> productFeignClient.defaultSku(new ProductIdDTO(productId)), "查询默认SKU失败");

    }



    public void increaseSales(String productId, int qty) {

        feignResponseSupport.run(

                () -> productFeignClient.increaseSales(new ProductSalesIncreaseDTO(productId, qty)),

                "增加销量失败");

    }

    public ProductSearchIndexVO getSearchIndex(String productId) {
        return feignResponseSupport.call(
                () -> productFeignClient.getSearchIndex(new ProductIdDTO(productId)),
                "查询搜索索引商品失败");
    }

    public ProductRagIndexVO getRagIndex(String productId) {
        return feignResponseSupport.call(
                () -> productFeignClient.getRagIndex(new ProductIdDTO(productId)),
                "查询RAG索引商品失败");
    }

    public PaginationResultVO<ProductSkuListVO> lessStockSkuPage(Integer pageNo, Integer pageSize, Integer threshold) {
        return feignResponseSupport.call(
                () -> productFeignClient.lessStockSkuPage(new LessStockPageDTO(pageNo, pageSize, threshold)),
                "查询低库存商品失败");
    }

    public List<String> listOnSaleProductIds() {
        List<String> ids = feignResponseSupport.call(
                productFeignClient::listOnSaleProductIds,
                "查询在售商品ID失败");
        return ids == null ? Collections.emptyList() : ids;
    }

    private static String nullToEmpty(String s) {

        return s == null ? "" : s;

    }

}

