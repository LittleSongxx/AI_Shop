package com.aishop.api.fallback;

import com.aishop.api.ProductFeignClient;
import com.aishop.api.dto.LessStockPageDTO;
import com.aishop.api.dto.ProductIdDTO;
import com.aishop.api.dto.ProductIdListDTO;
import com.aishop.api.dto.ProductSalesIncreaseDTO;
import com.aishop.api.dto.ProductSnapshotBatchVO;
import com.aishop.api.support.FeignFallbackResponses;
import com.aishop.api.vo.ProductRagIndexVO;
import com.aishop.api.vo.ProductSearchIndexVO;
import com.aishop.api.vo.ProductSkuSnapshotVO;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.api.vo.ProductSkuListVO;
import com.aishop.entity.vo.ResponseVO;
import lombok.extern.slf4j.Slf4j;
import org.springframework.cloud.openfeign.FallbackFactory;
import org.springframework.stereotype.Component;

@Slf4j
@Component
public class ProductFeignFallbackFactory implements FallbackFactory<ProductFeignClient> {

    @Override
    public ProductFeignClient create(Throwable cause) {
        log.warn("Product Feign fallback: {}", cause == null ? "unknown" : cause.toString());
        return new ProductFeignClient() {
            @Override
            public ResponseVO<ProductSnapshotBatchVO> snapshotBatch(ProductIdListDTO dto) {
                return FeignFallbackResponses.unavailable("商品服务");
            }

            @Override
            public ResponseVO<ProductSkuSnapshotVO> defaultSku(ProductIdDTO dto) {
                return FeignFallbackResponses.unavailable("商品服务");
            }

            @Override
            public ResponseVO<Void> increaseSales(ProductSalesIncreaseDTO dto) {
                return FeignFallbackResponses.unavailable("商品服务");
            }

            @Override
            public ResponseVO<ProductSearchIndexVO> getSearchIndex(ProductIdDTO dto) {
                return FeignFallbackResponses.unavailable("商品服务");
            }

            @Override
            public ResponseVO<ProductRagIndexVO> getRagIndex(ProductIdDTO dto) {
                return FeignFallbackResponses.unavailable("商品服务");
            }

            @Override
            public ResponseVO<PaginationResultVO<ProductSkuListVO>> lessStockSkuPage(LessStockPageDTO dto) {
                return FeignFallbackResponses.unavailable("商品服务");
            }

            @Override
            public ResponseVO<java.util.List<String>> listOnSaleProductIds() {
                return FeignFallbackResponses.unavailable("商品服务");
            }
        };
    }
}
