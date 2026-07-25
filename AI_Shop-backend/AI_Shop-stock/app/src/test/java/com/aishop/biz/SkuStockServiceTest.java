package com.aishop.biz;

import com.aishop.api.dto.RefundStockRestoreDTO;
import com.aishop.mappers.SkuStockMapper;
import com.aishop.mappers.StockChangeRecordMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class SkuStockServiceTest {

    @Mock
    private SkuStockMapper skuStockMapper;
    @Mock
    private StockChangeRecordMapper stockChangeRecordMapper;
    @InjectMocks
    private SkuStockService service;

    @Test
    void duplicateRefundBusinessKeyDoesNotRestoreTwice() {
        RefundStockRestoreDTO dto = refund("refund-1");
        when(stockChangeRecordMapper.insertIgnore(
                "refund-1", "REFUND_RESTORE", "p1", "sku1", 2))
                .thenReturn(0);

        assertEquals(0, service.restoreRefundStock(dto));
        verify(skuStockMapper, never()).changeStock("p1", "sku1", 2);
    }

    @Test
    void firstRefundRestoreChangesStockInSameTransaction() {
        RefundStockRestoreDTO dto = refund("refund-2");
        when(stockChangeRecordMapper.insertIgnore(
                "refund-2", "REFUND_RESTORE", "p1", "sku1", 2))
                .thenReturn(1);
        when(skuStockMapper.changeStock("p1", "sku1", 2)).thenReturn(1);

        assertEquals(1, service.restoreRefundStock(dto));
    }

    private static RefundStockRestoreDTO refund(String key) {
        RefundStockRestoreDTO dto = new RefundStockRestoreDTO();
        dto.setRefundRequestId(key);
        dto.setBusinessKey(key);
        dto.setProductId("p1");
        dto.setPropertyValueIdHash("sku1");
        dto.setChangeAmount(2);
        return dto;
    }
}
