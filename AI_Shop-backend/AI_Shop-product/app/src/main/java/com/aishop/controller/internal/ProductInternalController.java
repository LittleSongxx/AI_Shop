package com.aishop.controller.internal;

import com.aishop.api.dto.LessStockPageDTO;
import com.aishop.api.dto.ProductIdDTO;
import com.aishop.api.dto.ProductIdListDTO;
import com.aishop.api.dto.ProductSalesIncreaseDTO;
import com.aishop.api.dto.ProductSnapshotBatchVO;
import com.aishop.api.vo.ProductRagIndexVO;
import com.aishop.api.vo.ProductSearchIndexVO;
import com.aishop.api.vo.ProductSkuSnapshotVO;
import com.aishop.biz.ProductInternalService;
import com.aishop.biz.ProductSkuService;
import com.aishop.controller.ABaseController;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.api.vo.ProductSkuListVO;
import com.aishop.entity.vo.ResponseVO;
import jakarta.annotation.Resource;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/internal/product")
public class ProductInternalController extends ABaseController {

    @Resource
    private ProductInternalService productInternalService;

    @Resource
    private ProductSkuService productSkuService;

    @PostMapping("/snapshotBatch")
    public ResponseVO<ProductSnapshotBatchVO> snapshotBatch(@Valid @RequestBody ProductIdListDTO dto) {
        return getSuccessResponseVO(productInternalService.snapshotBatch(
                dto == null ? null : dto.getProductIds()));
    }

    @PostMapping("/defaultSku")
    public ResponseVO<ProductSkuSnapshotVO> defaultSku(@Valid @RequestBody ProductIdDTO dto) {
        return getSuccessResponseVO(productInternalService.defaultSku(dto.getProductId()));
    }

    @PostMapping("/increaseSales")
    public ResponseVO<Void> increaseSales(@Valid @RequestBody ProductSalesIncreaseDTO dto) {
        if (dto != null) {
            productInternalService.increaseSales(dto.getProductId(),
                    dto.getQty() == null ? 0 : dto.getQty());
        }
        return getSuccessResponseVO(null);
    }

    @PostMapping("/searchIndex")
    public ResponseVO<ProductSearchIndexVO> getSearchIndex(@Valid @RequestBody ProductIdDTO dto) {
        return getSuccessResponseVO(productInternalService.getSearchIndex(dto.getProductId()));
    }

    @PostMapping("/ragIndex")
    public ResponseVO<ProductRagIndexVO> getRagIndex(@Valid @RequestBody ProductIdDTO dto) {
        return getSuccessResponseVO(productInternalService.getRagIndex(dto.getProductId()));
    }

    @PostMapping("/lessStockSkuPage")
    public ResponseVO<PaginationResultVO<ProductSkuListVO>> lessStockSkuPage(@RequestBody LessStockPageDTO dto) {
        if (dto == null) {
            dto = new LessStockPageDTO();
        }
        return getSuccessResponseVO(productSkuService.lessStockSkuPage(
                dto.getPageNo(), dto.getPageSize(), dto.getThreshold()));
    }

    @PostMapping("/listOnSaleProductIds")
    public ResponseVO<List<String>> listOnSaleProductIds() {
        return getSuccessResponseVO(productInternalService.listOnSaleProductIds());
    }
}
