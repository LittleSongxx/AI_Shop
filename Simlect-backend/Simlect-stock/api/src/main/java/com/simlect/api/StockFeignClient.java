package com.simlect.api;

import com.simlect.api.dto.LessStockPageDTO;
import com.simlect.api.dto.SkuStockBatchChangeDTO;
import com.simlect.api.dto.SkuStockChangeDTO;
import com.simlect.api.dto.SkuStockDTO;
import com.simlect.api.dto.SkuStockQueryDTO;
import com.simlect.api.dto.SkuStockSetDTO;
import com.simlect.api.dto.ProductIdDTO;
import com.simlect.api.dto.RefundStockRestoreDTO;
import com.simlect.api.vo.ProductTotalStockVO;
import com.simlect.api.vo.StockChangeResultVO;
import com.simlect.api.fallback.StockFeignFallbackFactory;
import com.simlect.entity.vo.PaginationResultVO;
import com.simlect.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

import java.util.List;

@FeignClient(name = "simlect-stock", contextId = "stockFeignClient", path = "/internal/stock",
        fallbackFactory = StockFeignFallbackFactory.class)
public interface StockFeignClient {

    @PostMapping("/get")
    ResponseVO<SkuStockDTO> getStock(@RequestBody SkuStockQueryDTO dto);

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
