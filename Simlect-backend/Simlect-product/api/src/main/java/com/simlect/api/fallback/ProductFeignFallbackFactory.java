package com.simlect.api.fallback;

import com.simlect.api.ProductFeignClient;
import com.simlect.api.dto.LessStockPageDTO;
import com.simlect.api.dto.ProductIdDTO;
import com.simlect.api.dto.ProductIdListDTO;
import com.simlect.api.dto.ProductSalesIncreaseDTO;
import com.simlect.api.dto.ProductSnapshotBatchVO;
import com.simlect.api.support.FeignFallbackResponses;
import com.simlect.api.vo.ProductRagIndexVO;
import com.simlect.api.vo.ProductSearchIndexVO;
import com.simlect.api.vo.ProductSkuSnapshotVO;
import com.simlect.entity.vo.PaginationResultVO;
import com.simlect.api.vo.ProductSkuListVO;
import com.simlect.entity.vo.ResponseVO;
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
