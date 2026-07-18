package com.myshop.api;

import com.myshop.api.dto.LessStockPageDTO;
import com.myshop.api.dto.SkuStockBatchChangeDTO;
import com.myshop.api.dto.SkuStockChangeDTO;
import com.myshop.api.dto.SkuStockDTO;
import com.myshop.api.dto.SkuStockQueryDTO;
import com.myshop.api.dto.SkuStockSetDTO;
import com.myshop.api.dto.ProductIdDTO;
import com.myshop.api.vo.ProductTotalStockVO;
import com.myshop.api.vo.StockChangeResultVO;
import com.myshop.api.fallback.StockFeignFallbackFactory;
import com.myshop.entity.vo.PaginationResultVO;
import com.myshop.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

@FeignClient(name = "simlect-stock", contextId = "stockFeignClient", path = "/internal/stock",
        fallbackFactory = StockFeignFallbackFactory.class)
public interface StockFeignClient {

    @PostMapping("/get")
    ResponseVO<SkuStockDTO> getStock(@RequestBody SkuStockQueryDTO dto);

    @PostMapping("/change")
    ResponseVO<StockChangeResultVO> changeStock(@RequestBody SkuStockChangeDTO dto);

    @PostMapping("/changeBatch")
    ResponseVO<StockChangeResultVO> changeStockBatch(@RequestBody SkuStockBatchChangeDTO dto);

    @PostMapping("/lockAndVerify")
    ResponseVO<Void> lockAndVerify(@RequestBody SkuStockBatchChangeDTO dto);

    @PostMapping("/set")
    ResponseVO<Void> setStock(@RequestBody SkuStockSetDTO dto);

    @PostMapping("/totalByProduct")
    ResponseVO<ProductTotalStockVO> totalByProduct(@RequestBody ProductIdDTO dto);

    @PostMapping("/listLessThan")
    ResponseVO<PaginationResultVO<SkuStockDTO>> listLessThan(@RequestBody LessStockPageDTO dto);
}
