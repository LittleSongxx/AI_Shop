package com.simlect.api.support;

import com.simlect.api.StockFeignClient;
import com.simlect.api.dto.LessStockPageDTO;
import com.simlect.api.dto.ProductIdDTO;
import com.simlect.api.dto.SkuStockBatchChangeDTO;
import com.simlect.api.dto.SkuStockChangeDTO;
import com.simlect.api.dto.SkuStockDTO;
import com.simlect.api.dto.SkuStockQueryDTO;
import com.simlect.api.dto.SkuStockSetDTO;
import com.simlect.api.vo.ProductTotalStockVO;
import com.simlect.api.vo.StockChangeResultVO;
import com.simlect.compensation.StockBatchCompensatePort;
import com.simlect.entity.po.ProductItem;
import com.simlect.entity.vo.PaginationResultVO;
import com.simlect.exception.BusinessException;
import com.simlect.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

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
