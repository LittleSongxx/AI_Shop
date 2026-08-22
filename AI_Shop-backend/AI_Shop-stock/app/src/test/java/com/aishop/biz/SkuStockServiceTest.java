package com.aishop.biz;

import com.aishop.api.dto.OrderStockRestoreDTO;
import com.aishop.api.dto.RefundStockRestoreDTO;
import com.aishop.api.dto.SkuStockChangeDTO;
import com.aishop.mappers.SkuStockMapper;
import com.aishop.mappers.StockChangeRecordMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.ArgumentMatchers.eq;
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

    @Test
    void orderRestoreMergesSkuQuantityAndUsesStableBusinessKey() {
        OrderStockRestoreDTO dto = orderRestore("pay-1", 2, 3);
        when(stockChangeRecordMapper.insertIgnore(
                argThat(key -> key.startsWith("order-close:") && key.length() == 76),
                eq("ORDER_CLOSE_RESTORE"), eq("p1"), eq("sku1"), eq(5)))
                .thenReturn(1);
        when(skuStockMapper.changeStock("p1", "sku1", 5)).thenReturn(1);

        assertEquals(1, service.restoreOrderStock(dto));
        verify(skuStockMapper).changeStock("p1", "sku1", 5);
    }

    @Test
    void duplicateOrderRestoreDoesNotRestoreSkuTwice() {
        OrderStockRestoreDTO dto = orderRestore("pay-2", 4);
        when(stockChangeRecordMapper.insertIgnore(
                argThat(key -> key.startsWith("order-close:")),
                eq("ORDER_CLOSE_RESTORE"), eq("p1"), eq("sku1"), eq(4)))
                .thenReturn(0);

        assertEquals(0, service.restoreOrderStock(dto));
        verify(skuStockMapper, never()).changeStock("p1", "sku1", 4);
    }

    @Test
    void differentPaymentAggregatesUseDifferentRestoreKeys() {
        OrderStockRestoreDTO first = orderRestore("pay-a", 1);
        OrderStockRestoreDTO second = orderRestore("pay-b", 1);
        when(stockChangeRecordMapper.insertIgnore(
                argThat(key -> key != null), eq("ORDER_CLOSE_RESTORE"),
                eq("p1"), eq("sku1"), eq(1)))
                .thenReturn(0);

        service.restoreOrderStock(first);
        service.restoreOrderStock(second);

        var keyCaptor = org.mockito.ArgumentCaptor.forClass(String.class);
        verify(stockChangeRecordMapper, org.mockito.Mockito.times(2)).insertIgnore(
                keyCaptor.capture(), eq("ORDER_CLOSE_RESTORE"),
                eq("p1"), eq("sku1"), eq(1));
        assertEquals(2, keyCaptor.getAllValues().stream().distinct().count());
        assertTrue(keyCaptor.getAllValues().stream().allMatch(key -> key.length() == 76));
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

    private static OrderStockRestoreDTO orderRestore(String payOrderId, int... quantities) {
        OrderStockRestoreDTO dto = new OrderStockRestoreDTO();
        dto.setPayOrderId(payOrderId);
        dto.setItems(java.util.Arrays.stream(quantities)
                .mapToObj(quantity -> {
                    SkuStockChangeDTO item = new SkuStockChangeDTO();
                    item.setProductId("p1");
                    item.setPropertyValueIdHash("sku1");
                    item.setChangeAmount(quantity);
                    return item;
                })
                .toList());
        return dto;
    }
}
