package com.aishop.api.fallback;

import com.aishop.api.StockFeignClient;
import com.aishop.api.dto.LessStockPageDTO;
import com.aishop.api.dto.OrderStockRestoreDTO;
import com.aishop.api.dto.ProductIdDTO;
import com.aishop.api.dto.RefundStockRestoreDTO;
import com.aishop.api.dto.SkuStockBatchChangeDTO;
import com.aishop.api.dto.SkuStockChangeDTO;
import com.aishop.api.dto.SkuStockDTO;
import com.aishop.api.dto.SkuStockQueryDTO;
import com.aishop.api.dto.SkuStockSetDTO;
import com.aishop.api.support.FeignFallbackResponses;
import com.aishop.api.vo.ProductTotalStockVO;
import com.aishop.api.vo.StockChangeResultVO;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.entity.vo.ResponseVO;
import lombok.extern.slf4j.Slf4j;
import org.springframework.cloud.openfeign.FallbackFactory;
import org.springframework.stereotype.Component;

import java.util.List;

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
            public ResponseVO<List<SkuStockDTO>> getStockBatch(List<SkuStockQueryDTO> items) {
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
            public ResponseVO<StockChangeResultVO> restoreRefundStock(RefundStockRestoreDTO dto) {
                return FeignFallbackResponses.unavailable("库存服务");
            }

            @Override
            public ResponseVO<StockChangeResultVO> restoreOrderStock(OrderStockRestoreDTO dto) {
                return FeignFallbackResponses.unavailable("库存服务");
            }

            @Override
            public ResponseVO<Boolean> isRefundStockApplied(RefundStockRestoreDTO dto) {
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
            public ResponseVO<List<ProductTotalStockVO>> totalByProducts(List<String> productIds) {
                return FeignFallbackResponses.unavailable("库存服务");
            }

            @Override
            public ResponseVO<PaginationResultVO<SkuStockDTO>> listLessThan(LessStockPageDTO dto) {
                return FeignFallbackResponses.unavailable("库存服务");
            }
        };
    }
}
