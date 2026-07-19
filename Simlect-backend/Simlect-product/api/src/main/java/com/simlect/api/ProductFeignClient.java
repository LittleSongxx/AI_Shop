package com.simlect.api;

import com.simlect.api.dto.LessStockPageDTO;
import com.simlect.api.dto.ProductIdDTO;
import com.simlect.api.dto.ProductIdListDTO;
import com.simlect.api.dto.ProductSalesIncreaseDTO;
import com.simlect.api.dto.ProductSnapshotBatchVO;
import com.simlect.api.vo.ProductRagIndexVO;
import com.simlect.api.vo.ProductSearchIndexVO;
import com.simlect.api.vo.ProductSkuSnapshotVO;
import com.simlect.api.fallback.ProductFeignFallbackFactory;
import com.simlect.entity.vo.PaginationResultVO;
import com.simlect.api.vo.ProductSkuListVO;
import com.simlect.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

import java.util.List;

@FeignClient(name = "simlect-product", contextId = "productFeignClient", path = "/internal/product",
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
