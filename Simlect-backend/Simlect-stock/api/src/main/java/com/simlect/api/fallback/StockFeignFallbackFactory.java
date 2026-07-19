package com.simlect.api.fallback;

import com.simlect.api.StockFeignClient;
import com.simlect.api.dto.LessStockPageDTO;
import com.simlect.api.dto.ProductIdDTO;
import com.simlect.api.dto.SkuStockBatchChangeDTO;
import com.simlect.api.dto.SkuStockChangeDTO;
import com.simlect.api.dto.SkuStockDTO;
import com.simlect.api.dto.SkuStockQueryDTO;
import com.simlect.api.dto.SkuStockSetDTO;
import com.simlect.api.support.FeignFallbackResponses;
import com.simlect.api.vo.ProductTotalStockVO;
import com.simlect.api.vo.StockChangeResultVO;
import com.simlect.entity.vo.PaginationResultVO;
import com.simlect.entity.vo.ResponseVO;
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
