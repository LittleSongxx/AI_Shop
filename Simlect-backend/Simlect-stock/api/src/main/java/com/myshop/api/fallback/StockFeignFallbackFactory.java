package com.myshop.api.fallback;

import com.myshop.api.StockFeignClient;
import com.myshop.api.dto.LessStockPageDTO;
import com.myshop.api.dto.ProductIdDTO;
import com.myshop.api.dto.SkuStockBatchChangeDTO;
import com.myshop.api.dto.SkuStockChangeDTO;
import com.myshop.api.dto.SkuStockDTO;
import com.myshop.api.dto.SkuStockQueryDTO;
import com.myshop.api.dto.SkuStockSetDTO;
import com.myshop.api.support.FeignFallbackResponses;
import com.myshop.api.vo.ProductTotalStockVO;
import com.myshop.api.vo.StockChangeResultVO;
import com.myshop.entity.vo.PaginationResultVO;
import com.myshop.entity.vo.ResponseVO;
import lombok.extern.slf4j.Slf4j;
import org.springframework.cloud.openfeign.FallbackFactory;
import org.springframework.stereotype.Component;

@Slf4j
@Component
public class StockFeignFallbackFactory implements FallbackFactory<StockFeignClient> {

    @Override
    public StockFeignClient create(Throwable cause) {
        log.warn("Stock Feign fallback: {}", cause == null ? "unknown" : cause.toString());
        return new StockFeignClient() {
            @Override
            public ResponseVO<SkuStockDTO> getStock(SkuStockQueryDTO dto) {
                return FeignFallbackResponses.unavailable("库存服务");
            }

            @Override
            public ResponseVO<StockChangeResultVO> changeStock(SkuStockChangeDTO dto) {
                return FeignFallbackResponses.unavailable("库存服务");
            }

            @Override
            public ResponseVO<StockChangeResultVO> changeStockBatch(SkuStockBatchChangeDTO dto) {
                return FeignFallbackResponses.unavailable("库存服务");
            }

            @Override
            public ResponseVO<Void> lockAndVerify(SkuStockBatchChangeDTO dto) {
                return FeignFallbackResponses.unavailable("库存服务");
            }

            @Override
            public ResponseVO<Void> setStock(SkuStockSetDTO dto) {
                return FeignFallbackResponses.unavailable("库存服务");
            }

            @Override
            public ResponseVO<ProductTotalStockVO> totalByProduct(ProductIdDTO dto) {
                return FeignFallbackResponses.unavailable("库存服务");
            }

            @Override
            public ResponseVO<PaginationResultVO<SkuStockDTO>> listLessThan(LessStockPageDTO dto) {
                return FeignFallbackResponses.unavailable("库存服务");
            }
        };
    }
}
