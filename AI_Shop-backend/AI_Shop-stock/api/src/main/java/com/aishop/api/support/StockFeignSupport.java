package com.aishop.api.support;

import com.aishop.api.StockFeignClient;
import com.aishop.api.dto.LessStockPageDTO;
import com.aishop.api.dto.OrderStockRestoreDTO;
import com.aishop.api.dto.ProductIdDTO;
import com.aishop.api.dto.RefundStockRestoreDTO;
import com.aishop.api.dto.SkuStockBatchChangeDTO;
import com.aishop.api.dto.SkuStockChangeDTO;
import com.aishop.api.dto.SkuStockDTO;
import com.aishop.api.dto.SkuStockSetDTO;
import com.aishop.api.dto.SkuStockQueryDTO;
import com.aishop.api.vo.ProductTotalStockVO;
import com.aishop.api.vo.StockChangeResultVO;
import com.aishop.compensation.StockBatchCompensatePort;
import com.aishop.entity.po.ProductItem;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.exception.BusinessException;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Component
public class StockFeignSupport implements StockBatchCompensatePort {

    @Resource
    private StockFeignClient stockFeignClient;
    @Resource
    private FeignResponseSupport feignResponseSupport;

    public int getAvailable(String productId, String propertyValueIdHash) {
        SkuStockDTO data = feignResponseSupport.call(
                () -> stockFeignClient.getStock(new SkuStockQueryDTO(productId, propertyValueIdHash)),
                "查询库存失败");
        return data == null || data.getStock() == null ? 0 : data.getStock();
    }

    public Map<String, Integer> getAvailableBatch(List<SkuStockQueryDTO> items) {
        if (items == null || items.isEmpty()) {
            return Collections.emptyMap();
        }
        List<SkuStockDTO> rows = feignResponseSupport.call(
                () -> stockFeignClient.getStockBatch(items), "批量查询SKU库存失败");
        Map<String, Integer> result = new HashMap<>();
        for (SkuStockQueryDTO item : items) {
            if (item != null && item.getPropertyValueIdHash() != null) {
                result.putIfAbsent(item.getPropertyValueIdHash(), 0);
            }
        }
        if (rows != null) {
            for (SkuStockDTO row : rows) {
                if (row != null && row.getPropertyValueIdHash() != null) {
                    result.put(row.getPropertyValueIdHash(),
                            row.getStock() == null ? 0 : row.getStock());
                }
            }
        }
        return result;
    }

    public void lockAndVerify(List<ProductItem> items) {
        feignResponseSupport.run(() -> stockFeignClient.lockAndVerify(toBatch(items, true)), "库存校验失败");
    }

    @Override
    public int changeStockBatch(List<ProductItem> items) {
        StockChangeResultVO result = feignResponseSupport.call(
                () -> stockFeignClient.changeStockBatch(toBatch(items, false)), "库存变更失败");
        return result == null || result.getAffectedRows() == null ? 0 : result.getAffectedRows();
    }

    public void changeStock(String productId, String propertyValueIdHash, int changeAmount) {
        SkuStockChangeDTO dto = new SkuStockChangeDTO();
        dto.setProductId(productId);
        dto.setPropertyValueIdHash(propertyValueIdHash);
        dto.setChangeAmount(changeAmount);
        feignResponseSupport.run(() -> stockFeignClient.changeStock(dto), "库存变更失败");
    }

    public int restoreRefundStock(RefundStockRestoreDTO dto) {
        StockChangeResultVO result = feignResponseSupport.call(
                () -> stockFeignClient.restoreRefundStock(dto), "退款库存恢复失败");
        return result == null || result.getAffectedRows() == null ? 0 : result.getAffectedRows();
    }

    @Override
    public int restoreOrderStock(String payOrderId, List<ProductItem> items) {
        OrderStockRestoreDTO dto = new OrderStockRestoreDTO();
        dto.setPayOrderId(payOrderId);
        dto.setItems(toBatch(items, true).getItems());
        StockChangeResultVO result = feignResponseSupport.call(
                () -> stockFeignClient.restoreOrderStock(dto), "关单库存恢复失败");
        return result == null || result.getAffectedRows() == null ? 0 : result.getAffectedRows();
    }

    public boolean isRefundStockApplied(String businessKey) {
        RefundStockRestoreDTO dto = new RefundStockRestoreDTO();
        dto.setBusinessKey(businessKey);
        Boolean result = feignResponseSupport.call(
                () -> stockFeignClient.isRefundStockApplied(dto), "查询退款库存恢复状态失败");
        return Boolean.TRUE.equals(result);
    }

    public void setStock(String productId, String propertyValueIdHash, int stock) {
        feignResponseSupport.run(
                () -> stockFeignClient.setStock(new SkuStockSetDTO(productId, propertyValueIdHash, stock)),
                "设置库存失败");
    }

    public int totalByProduct(String productId) {
        ProductTotalStockVO vo = feignResponseSupport.call(
                () -> stockFeignClient.totalByProduct(new ProductIdDTO(productId)), "查询商品总库存失败");
        return vo == null || vo.getTotalStock() == null ? 0 : vo.getTotalStock();
    }

    public Map<String, Integer> totalByProducts(List<String> productIds) {
        if (productIds == null || productIds.isEmpty()) {
            return Collections.emptyMap();
        }
        try {
            List<ProductTotalStockVO> rows = feignResponseSupport.call(
                    () -> stockFeignClient.totalByProducts(productIds),
                    "批量查询商品库存失败");
            Map<String, Integer> result = new HashMap<>();
            if (rows != null) {
                for (ProductTotalStockVO row : rows) {
                    if (row != null && row.getProductId() != null) {
                        result.put(
                                row.getProductId(),
                                row.getTotalStock() == null ? 0 : row.getTotalStock());
                    }
                }
            }
            return result;
        } catch (RuntimeException ignored) {
            // Stock is advisory for recommendation cards; product search remains available.
            return Collections.emptyMap();
        }
    }

    public PaginationResultVO<SkuStockDTO> listLessThan(Integer pageNo, Integer pageSize, Integer threshold) {
        PaginationResultVO<SkuStockDTO> page = feignResponseSupport.call(
                () -> stockFeignClient.listLessThan(new LessStockPageDTO(pageNo, pageSize, threshold)),
                "查询低库存SKU失败");
        if (page == null) {
            return new PaginationResultVO<>(0, pageSize == null ? 15 : pageSize,
                    pageNo == null ? 1 : pageNo, 0, List.of());
        }
        return page;
    }

    private SkuStockBatchChangeDTO toBatch(List<ProductItem> items, boolean absForVerify) {
        if (items == null || items.isEmpty()) {
            throw new BusinessException("商品列表为空");
        }
        List<SkuStockChangeDTO> list = new ArrayList<>(items.size());
        for (ProductItem item : items) {
            if (StringTools.isEmpty(item.getProductId()) || StringTools.isEmpty(item.getPropertyValueIdHash())) {
                throw new BusinessException("商品sku不存在");
            }
            SkuStockChangeDTO dto = new SkuStockChangeDTO();
            dto.setProductId(item.getProductId());
            dto.setPropertyValueIdHash(item.getPropertyValueIdHash());
            int amount = item.getBuyCount() == null ? 0 : item.getBuyCount();
            dto.setChangeAmount(absForVerify ? Math.abs(amount) : amount);
            list.add(dto);
        }
        SkuStockBatchChangeDTO batch = new SkuStockBatchChangeDTO();
        batch.setItems(list);
        return batch;
    }
}
