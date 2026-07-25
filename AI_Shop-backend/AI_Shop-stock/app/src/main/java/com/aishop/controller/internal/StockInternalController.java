package com.aishop.controller.internal;

import com.aishop.api.dto.LessStockPageDTO;
import com.aishop.api.dto.ProductIdDTO;
import com.aishop.api.dto.RefundStockRestoreDTO;
import com.aishop.api.dto.SkuStockBatchChangeDTO;
import com.aishop.api.dto.SkuStockChangeDTO;
import com.aishop.api.dto.SkuStockDTO;
import com.aishop.api.dto.SkuStockQueryDTO;
import com.aishop.api.dto.SkuStockSetDTO;
import com.aishop.api.vo.ProductTotalStockVO;
import com.aishop.api.vo.StockChangeResultVO;
import com.aishop.biz.SkuStockService;
import com.aishop.controller.ABaseController;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.entity.vo.ResponseVO;
import jakarta.annotation.Resource;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/internal/stock")
public class StockInternalController extends ABaseController {

    @Resource
    private SkuStockService skuStockService;

    @PostMapping("/get")
    public ResponseVO<SkuStockDTO> getStock(@Valid @RequestBody SkuStockQueryDTO dto) {
        return getSuccessResponseVO(skuStockService.getStock(dto.getProductId(), dto.getPropertyValueIdHash()));
    }

    @PostMapping("/getBatch")
    public ResponseVO<List<SkuStockDTO>> getStockBatch(@RequestBody List<SkuStockQueryDTO> items) {
        return getSuccessResponseVO(skuStockService.getStockBatch(items));
    }

    @PostMapping("/change")
    public ResponseVO<StockChangeResultVO> changeStock(@Valid @RequestBody SkuStockChangeDTO dto) {
        return getSuccessResponseVO(new StockChangeResultVO(skuStockService.changeStock(dto)));
    }

    @PostMapping("/changeBatch")
    public ResponseVO<StockChangeResultVO> changeStockBatch(@Valid @RequestBody SkuStockBatchChangeDTO dto) {
        return getSuccessResponseVO(new StockChangeResultVO(skuStockService.changeStockBatch(dto)));
    }

    @PostMapping("/refund/restore")
    public ResponseVO<StockChangeResultVO> restoreRefundStock(
            @Valid @RequestBody RefundStockRestoreDTO dto) {
        return getSuccessResponseVO(new StockChangeResultVO(skuStockService.restoreRefundStock(dto)));
    }

    @PostMapping("/refund/applied")
    public ResponseVO<Boolean> isRefundStockApplied(@RequestBody RefundStockRestoreDTO dto) {
        return getSuccessResponseVO(dto != null
                && skuStockService.isRefundStockApplied(dto.getBusinessKey()));
    }

    @PostMapping("/lockAndVerify")
    public ResponseVO<Void> lockAndVerify(@Valid @RequestBody SkuStockBatchChangeDTO dto) {
        skuStockService.lockAndVerify(dto);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/set")
    public ResponseVO<Void> setStock(@Valid @RequestBody SkuStockSetDTO dto) {
        skuStockService.setStock(dto.getProductId(), dto.getPropertyValueIdHash(), dto.getStock());
        return getSuccessResponseVO(null);
    }

    @PostMapping("/totalByProduct")
    public ResponseVO<ProductTotalStockVO> totalByProduct(@Valid @RequestBody ProductIdDTO dto) {
        return getSuccessResponseVO(new ProductTotalStockVO(dto.getProductId(),
                skuStockService.totalByProductId(dto.getProductId())));
    }

    @PostMapping("/totalByProducts")
    public ResponseVO<List<ProductTotalStockVO>> totalByProducts(@RequestBody List<String> productIds) {
        Map<String, Integer> totals = skuStockService.totalByProductIds(productIds);
        List<ProductTotalStockVO> result = new ArrayList<>();
        for (Map.Entry<String, Integer> entry : totals.entrySet()) {
            result.add(new ProductTotalStockVO(entry.getKey(), entry.getValue()));
        }
        return getSuccessResponseVO(result);
    }

    @PostMapping("/listLessThan")
    public ResponseVO<PaginationResultVO<SkuStockDTO>> listLessThan(@RequestBody LessStockPageDTO dto) {
        if (dto == null) {
            dto = new LessStockPageDTO();
        }
        return getSuccessResponseVO(skuStockService.listLessThan(dto.getPageNo(), dto.getPageSize(), dto.getThreshold()));
    }
}
