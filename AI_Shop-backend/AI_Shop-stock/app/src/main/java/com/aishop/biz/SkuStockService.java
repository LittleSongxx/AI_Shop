package com.aishop.biz;

import com.aishop.api.dto.OrderStockRestoreDTO;
import com.aishop.api.dto.RefundStockRestoreDTO;
import com.aishop.api.dto.SkuStockBatchChangeDTO;
import com.aishop.api.dto.SkuStockChangeDTO;
import com.aishop.api.dto.SkuStockDTO;
import com.aishop.api.dto.SkuStockQueryDTO;
import com.aishop.api.vo.ProductTotalStockVO;
import com.aishop.domain.SkuStock;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.SkuStockMapper;
import com.aishop.mappers.StockChangeRecordMapper;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.CollectionUtils;

import java.util.ArrayList;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

@Service
public class SkuStockService {

    @Resource
    private SkuStockMapper skuStockMapper;
    @Resource
    private StockChangeRecordMapper stockChangeRecordMapper;

    public SkuStockDTO getStock(String productId, String propertyValueIdHash) {
        SkuStock row = skuStockMapper.selectByKey(productId, propertyValueIdHash);
        if (row == null) {
            return new SkuStockDTO(productId, propertyValueIdHash, 0);
        }
        return new SkuStockDTO(row.getProductId(), row.getPropertyValueIdHash(), row.getStock());
    }

    public List<SkuStockDTO> getStockBatch(List<SkuStockQueryDTO> items) {
        if (CollectionUtils.isEmpty(items)) {
            return Collections.emptyList();
        }
        Map<String, SkuStockDTO> result = new LinkedHashMap<>();
        List<SkuStockQueryDTO> validItems = new ArrayList<>();
        for (SkuStockQueryDTO item : items) {
            if (item == null || item.getProductId() == null || item.getProductId().isBlank()
                    || item.getPropertyValueIdHash() == null
                    || item.getPropertyValueIdHash().isBlank()) {
                continue;
            }
            String key = item.getProductId() + "\u0000" + item.getPropertyValueIdHash();
            result.putIfAbsent(key, new SkuStockDTO(item.getProductId(), item.getPropertyValueIdHash(), 0));
            validItems.add(item);
        }
        if (validItems.isEmpty()) {
            return Collections.emptyList();
        }
        List<SkuStock> rows = skuStockMapper.selectByKeys(validItems);
        if (rows != null) {
            for (SkuStock row : rows) {
                String key = row.getProductId() + "\u0000" + row.getPropertyValueIdHash();
                result.put(key, new SkuStockDTO(row.getProductId(), row.getPropertyValueIdHash(), row.getStock()));
            }
        }
        return new ArrayList<>(result.values());
    }

    @Transactional(rollbackFor = Exception.class)
    public int changeStock(SkuStockChangeDTO dto) {
        int affected = skuStockMapper.changeStock(dto.getProductId(), dto.getPropertyValueIdHash(), dto.getChangeAmount());
        if (affected <= 0) {
            throw new BusinessException("库存不足");
        }
        return affected;
    }

    @Transactional(rollbackFor = Exception.class)
    public void setStock(String productId, String propertyValueIdHash, Integer stock) {
        if (stock == null || stock < 0) {
            throw new BusinessException("库存最低为0");
        }
        skuStockMapper.upsert(productId, propertyValueIdHash, stock);
    }

    public int totalByProductId(String productId) {
        Integer total = skuStockMapper.selectTotalStockByProductId(productId);
        return total == null ? 0 : total;
    }

    public Map<String, Integer> totalByProductIds(List<String> productIds) {
        if (CollectionUtils.isEmpty(productIds)) {
            return Collections.emptyMap();
        }
        Map<String, Integer> result = new LinkedHashMap<>();
        for (String productId : productIds) {
            if (productId != null && !productId.trim().isEmpty()) {
                result.putIfAbsent(productId, 0);
            }
        }
        if (result.isEmpty()) {
            return Collections.emptyMap();
        }
        List<ProductTotalStockVO> rows =
                skuStockMapper.selectTotalStockByProductIds(new ArrayList<>(result.keySet()));
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
    }

    public com.aishop.entity.vo.PaginationResultVO<SkuStockDTO> listLessThan(Integer pageNo, Integer pageSize, Integer threshold) {
        int th = threshold == null ? 10 : threshold;
        int size = pageSize == null ? 15 : pageSize;
        int no = pageNo == null || pageNo < 1 ? 1 : pageNo;
        Integer countObj = skuStockMapper.countLessThan(th);
        int count = countObj == null ? 0 : countObj;
        com.aishop.entity.query.SimplePage page = new com.aishop.entity.query.SimplePage(no, count, size);
        List<SkuStock> rows = count == 0
                ? java.util.Collections.emptyList()
                : skuStockMapper.selectLessThan(th, page.getStart(), page.getEnd());
        List<SkuStockDTO> list = new java.util.ArrayList<>();
        if (rows != null) {
            for (SkuStock row : rows) {
                list.add(new SkuStockDTO(row.getProductId(), row.getPropertyValueIdHash(), row.getStock()));
            }
        }
        return new com.aishop.entity.vo.PaginationResultVO<>(count, page.getPageSize(), page.getPageNo(), page.getPageTotal(), list);
    }

    @Transactional(rollbackFor = Exception.class)
    public int changeStockBatch(SkuStockBatchChangeDTO batch) {
        if (batch == null || CollectionUtils.isEmpty(batch.getItems())) {
            throw new BusinessException("库存变更列表为空");
        }
        Map<String, Integer> merged = mergeBySku(batch.getItems());
        int total = 0;
        for (Map.Entry<String, Integer> entry : new TreeMap<>(merged).entrySet()) {
            String[] parts = entry.getKey().split("\0", 2);
            int affected = skuStockMapper.changeStock(parts[0], parts[1], entry.getValue());
            if (affected <= 0) {
                throw new BusinessException("库存不足");
            }
            total += affected;
        }
        return total;
    }

    @Transactional(rollbackFor = Exception.class)
    public int restoreRefundStock(RefundStockRestoreDTO dto) {
        if (dto == null || dto.getChangeAmount() == null || dto.getChangeAmount() <= 0) {
            throw new BusinessException("退款库存恢复数量必须大于0");
        }
        int inserted = stockChangeRecordMapper.insertIgnore(
                dto.getBusinessKey(),
                "REFUND_RESTORE",
                dto.getProductId(),
                dto.getPropertyValueIdHash(),
                dto.getChangeAmount());
        if (inserted == 0) {
            return 0;
        }
        int affected = skuStockMapper.changeStock(
                dto.getProductId(), dto.getPropertyValueIdHash(), dto.getChangeAmount());
        if (affected <= 0) {
            throw new BusinessException("退款库存恢复失败");
        }
        return affected;
    }

    @Transactional(rollbackFor = Exception.class)
    public int restoreOrderStock(OrderStockRestoreDTO dto) {
        if (dto == null || dto.getPayOrderId() == null || dto.getPayOrderId().isBlank()) {
            throw new BusinessException("支付订单号不能为空");
        }
        if (CollectionUtils.isEmpty(dto.getItems())) {
            throw new BusinessException("关单库存恢复列表为空");
        }

        Map<String, Integer> merged = new TreeMap<>();
        for (SkuStockChangeDTO item : dto.getItems()) {
            if (item == null || item.getProductId() == null || item.getProductId().isBlank()
                    || item.getPropertyValueIdHash() == null
                    || item.getPropertyValueIdHash().isBlank()) {
                throw new BusinessException("商品sku不存在");
            }
            if (item.getChangeAmount() == null || item.getChangeAmount() <= 0) {
                throw new BusinessException("关单库存恢复数量必须大于0");
            }
            String key = item.getProductId() + "\0" + item.getPropertyValueIdHash();
            try {
                merged.merge(key, item.getChangeAmount(), Math::addExact);
            } catch (ArithmeticException exception) {
                throw new BusinessException("关单库存恢复数量超出范围");
            }
        }

        int total = 0;
        for (Map.Entry<String, Integer> entry : merged.entrySet()) {
            String[] parts = entry.getKey().split("\0", 2);
            String businessKey = orderRestoreBusinessKey(
                    dto.getPayOrderId(), parts[0], parts[1]);
            int inserted = stockChangeRecordMapper.insertIgnore(
                    businessKey,
                    "ORDER_CLOSE_RESTORE",
                    parts[0],
                    parts[1],
                    entry.getValue());
            if (inserted == 0) {
                continue;
            }
            int affected = skuStockMapper.changeStock(parts[0], parts[1], entry.getValue());
            if (affected <= 0) {
                throw new BusinessException("关单库存恢复失败");
            }
            total += affected;
        }
        return total;
    }

    public boolean isRefundStockApplied(String businessKey) {
        return businessKey != null && stockChangeRecordMapper.exists(businessKey) > 0;
    }

    @Transactional(rollbackFor = Exception.class)
    public void lockAndVerify(SkuStockBatchChangeDTO batch) {
        if (batch == null || CollectionUtils.isEmpty(batch.getItems())) {
            throw new BusinessException("库存校验列表为空");
        }
        Map<String, Integer> needBySku = new TreeMap<>();
        Map<String, SkuStockChangeDTO> sample = new HashMap<>();
        for (SkuStockChangeDTO item : batch.getItems()) {
            int need = item.getChangeAmount() == null ? 0 : Math.abs(item.getChangeAmount());
            String key = item.getProductId() + "\0" + item.getPropertyValueIdHash();
            needBySku.merge(key, need, Integer::sum);
            sample.putIfAbsent(key, item);
        }
        for (Map.Entry<String, Integer> entry : needBySku.entrySet()) {
            SkuStockChangeDTO s = sample.get(entry.getKey());
            SkuStock locked = skuStockMapper.selectByKeyForUpdate(s.getProductId(), s.getPropertyValueIdHash());
            if (locked == null) {
                throw new BusinessException("商品sku不存在");
            }
            if (locked.getStock() < entry.getValue()) {
                throw new BusinessException("库存不足");
            }
        }
    }

    private Map<String, Integer> mergeBySku(List<SkuStockChangeDTO> items) {
        Map<String, Integer> merged = new HashMap<>();
        items.stream()
                .sorted(Comparator.comparing(SkuStockChangeDTO::getProductId)
                        .thenComparing(SkuStockChangeDTO::getPropertyValueIdHash))
                .forEach(item -> {
                    String key = item.getProductId() + "\0" + item.getPropertyValueIdHash();
                    merged.merge(key, item.getChangeAmount(), Integer::sum);
                });
        return merged;
    }

    private String orderRestoreBusinessKey(
            String payOrderId, String productId, String propertyValueIdHash) {
        String canonical = payOrderId + "\0" + productId + "\0" + propertyValueIdHash;
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(canonical.getBytes(StandardCharsets.UTF_8));
            return "order-close:" + HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 is unavailable", impossible);
        }
    }
}
