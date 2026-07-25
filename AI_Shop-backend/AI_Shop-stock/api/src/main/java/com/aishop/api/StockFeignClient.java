package com.aishop.api;

import com.aishop.api.dto.LessStockPageDTO;
import com.aishop.api.dto.SkuStockBatchChangeDTO;
import com.aishop.api.dto.SkuStockChangeDTO;
import com.aishop.api.dto.SkuStockDTO;
import com.aishop.api.dto.SkuStockQueryDTO;
import com.aishop.api.dto.SkuStockSetDTO;
import com.aishop.api.dto.ProductIdDTO;
import com.aishop.api.dto.RefundStockRestoreDTO;
import com.aishop.api.vo.ProductTotalStockVO;
import com.aishop.api.vo.StockChangeResultVO;
import com.aishop.api.fallback.StockFeignFallbackFactory;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

import java.util.List;

@FeignClient(name = "aishop-stock", contextId = "stockFeignClient", path = "/internal/stock",
        fallbackFactory = StockFeignFallbackFactory.class)
public interface StockFeignClient {

    @PostMapping("/get")
    ResponseVO<SkuStockDTO> getStock(@RequestBody SkuStockQueryDTO dto);

    @PostMapping("/getBatch")
    ResponseVO<List<SkuStockDTO>> getStockBatch(@RequestBody List<SkuStockQueryDTO> items);

    @PostMapping("/change")
    ResponseVO<StockChangeResultVO> changeStock(@RequestBody SkuStockChangeDTO dto);

    @PostMapping("/changeBatch")
    ResponseVO<StockChangeResultVO> changeStockBatch(@RequestBody SkuStockBatchChangeDTO dto);

    @PostMapping("/refund/restore")
    ResponseVO<StockChangeResultVO> restoreRefundStock(@RequestBody RefundStockRestoreDTO dto);

    @PostMapping("/refund/applied")
    ResponseVO<Boolean> isRefundStockApplied(@RequestBody RefundStockRestoreDTO dto);

    @PostMapping("/lockAndVerify")
    ResponseVO<Void> lockAndVerify(@RequestBody SkuStockBatchChangeDTO dto);

    @PostMapping("/set")
    ResponseVO<Void> setStock(@RequestBody SkuStockSetDTO dto);

    @PostMapping("/totalByProduct")
    ResponseVO<ProductTotalStockVO> totalByProduct(@RequestBody ProductIdDTO dto);

    @PostMapping("/totalByProducts")
    ResponseVO<List<ProductTotalStockVO>> totalByProducts(@RequestBody List<String> productIds);

    @PostMapping("/listLessThan")
    ResponseVO<PaginationResultVO<SkuStockDTO>> listLessThan(@RequestBody LessStockPageDTO dto);
}
