package com.aishop.api;

import com.aishop.api.dto.LessStockPageDTO;
import com.aishop.api.dto.ProductIdDTO;
import com.aishop.api.dto.ProductIdListDTO;
import com.aishop.api.dto.ProductSalesIncreaseDTO;
import com.aishop.api.dto.ProductSnapshotBatchVO;
import com.aishop.api.vo.ProductRagIndexVO;
import com.aishop.api.vo.ProductSearchIndexVO;
import com.aishop.api.vo.ProductSkuSnapshotVO;
import com.aishop.api.fallback.ProductFeignFallbackFactory;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.api.vo.ProductSkuListVO;
import com.aishop.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

import java.util.List;

@FeignClient(name = "aishop-product", contextId = "productFeignClient", path = "/internal/product",
        fallbackFactory = ProductFeignFallbackFactory.class)
public interface ProductFeignClient {

    @PostMapping("/snapshotBatch")
    ResponseVO<ProductSnapshotBatchVO> snapshotBatch(@RequestBody ProductIdListDTO dto);

    @PostMapping("/defaultSku")
    ResponseVO<ProductSkuSnapshotVO> defaultSku(@RequestBody ProductIdDTO dto);

    @PostMapping("/increaseSales")
    ResponseVO<Void> increaseSales(@RequestBody ProductSalesIncreaseDTO dto);

    @PostMapping("/searchIndex")
    ResponseVO<ProductSearchIndexVO> getSearchIndex(@RequestBody ProductIdDTO dto);

    @PostMapping("/ragIndex")
    ResponseVO<ProductRagIndexVO> getRagIndex(@RequestBody ProductIdDTO dto);

    @PostMapping("/lessStockSkuPage")
    ResponseVO<PaginationResultVO<ProductSkuListVO>> lessStockSkuPage(@RequestBody LessStockPageDTO dto);

    @PostMapping("/listOnSaleProductIds")
    ResponseVO<List<String>> listOnSaleProductIds();
}
