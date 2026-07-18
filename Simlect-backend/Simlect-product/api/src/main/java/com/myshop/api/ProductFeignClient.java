package com.myshop.api;

import com.myshop.api.dto.LessStockPageDTO;
import com.myshop.api.dto.ProductIdDTO;
import com.myshop.api.dto.ProductIdListDTO;
import com.myshop.api.dto.ProductSalesIncreaseDTO;
import com.myshop.api.dto.ProductSnapshotBatchVO;
import com.myshop.api.vo.ProductRagIndexVO;
import com.myshop.api.vo.ProductSearchIndexVO;
import com.myshop.api.vo.ProductSkuSnapshotVO;
import com.myshop.api.fallback.ProductFeignFallbackFactory;
import com.myshop.entity.vo.PaginationResultVO;
import com.myshop.entity.vo.ProductSkuListVO;
import com.myshop.entity.vo.ResponseVO;
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
