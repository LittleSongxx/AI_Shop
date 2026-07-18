package com.myshop.api.fallback;

import com.myshop.api.ProductFeignClient;
import com.myshop.api.dto.LessStockPageDTO;
import com.myshop.api.dto.ProductIdDTO;
import com.myshop.api.dto.ProductIdListDTO;
import com.myshop.api.dto.ProductSalesIncreaseDTO;
import com.myshop.api.dto.ProductSnapshotBatchVO;
import com.myshop.api.support.FeignFallbackResponses;
import com.myshop.api.vo.ProductRagIndexVO;
import com.myshop.api.vo.ProductSearchIndexVO;
import com.myshop.api.vo.ProductSkuSnapshotVO;
import com.myshop.entity.vo.PaginationResultVO;
import com.myshop.entity.vo.ProductSkuListVO;
import com.myshop.entity.vo.ResponseVO;
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
