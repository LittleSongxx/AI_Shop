package com.aishop.biz.impl;

import com.aishop.api.dto.PostOrderDTO;
import com.aishop.api.dto.ProductSnapshotBatchVO;
import com.aishop.api.enums.LogisticsStatusEnum;
import com.aishop.api.enums.OrderFromTypeEnum;
import com.aishop.api.enums.OrderStatusEnum;
import com.aishop.api.enums.ProductStatusEnum;
import com.aishop.api.support.PayFeignSupport;
import com.aishop.api.support.ProductFeignSupport;
import com.aishop.api.support.StockFeignSupport;
import com.aishop.api.support.UserFeignSupport;
import com.aishop.api.vo.ProductInfoSnapshotVO;
import com.aishop.api.vo.ProductPropertyValueSnapshotVO;
import com.aishop.api.vo.ProductSkuSnapshotVO;
import com.aishop.api.vo.UserAddressVO;
import com.aishop.component.RedisComponent;
import com.aishop.constants.TransactionalMqSender;
import com.aishop.entity.dto.LogisticsSendDTO;
import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.po.OrderItem;
import com.aishop.entity.po.OrderLogisticsInfo;
import com.aishop.entity.po.ProductItem;
import com.aishop.entity.query.OrderInfoQuery;
import com.aishop.entity.query.OrderItemQuery;
import com.aishop.entity.query.OrderLogisticsInfoQuery;
import com.aishop.mappers.OrderInfoMapper;
import com.aishop.mappers.OrderItemMapper;
import com.aishop.mappers.OrderLogisticsInfoMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OrderCreationIntegrityTest {

    @Mock
    private OrderInfoMapper<OrderInfo, OrderInfoQuery> orderInfoMapper;
    @Mock
    private OrderItemMapper<OrderItem, OrderItemQuery> orderItemMapper;
    @Mock
    private OrderLogisticsInfoMapper<OrderLogisticsInfo, OrderLogisticsInfoQuery> orderLogisticsInfoMapper;
    @Mock
    private ProductFeignSupport productFeignSupport;
    @Mock
    private StockFeignSupport stockFeignSupport;
    @Mock
    private UserFeignSupport userFeignSupport;
    @Mock
    private RedisComponent redisComponent;
    @Mock
    private PayFeignSupport payFeignSupport;
    @Mock
    private TransactionalMqSender transactionalMqSender;

    @InjectMocks
    private OrderInfoServiceImpl service;

    @Test
    void authoritativeSkuDrivesStockAndEveryOrderGetsLogistics() {
        ProductItem first = item("p1", "v1", "forged-hash-1");
        ProductItem second = item("p2", "v2", "forged-hash-2");
        PostOrderDTO request = request(List.of(first, second));
        ProductSnapshotBatchVO snapshot = new ProductSnapshotBatchVO();

        when(userFeignSupport.getAddress("address-1", "user-1")).thenReturn(address());
        when(productFeignSupport.snapshotBatch(List.of("p1", "p2"))).thenReturn(snapshot);
        when(productFeignSupport.toProductInfoMap(snapshot)).thenReturn(Map.of(
                "p1", product("p1", "Headphones"),
                "p2", product("p2", "Coat")));
        when(productFeignSupport.toPropertyValueMap(snapshot)).thenReturn(Map.of(
                "p1v1", property("p1", "v1"),
                "p2v2", property("p2", "v2")));
        when(productFeignSupport.toSkuMapByPropertyValueIds(snapshot)).thenReturn(Map.of(
                "p1v1", sku("p1", "v1", "trusted-hash-1", "10.00"),
                "p2v2", sku("p2", "v2", "trusted-hash-2", "20.00")));
        when(stockFeignSupport.getAvailable("p1", "trusted-hash-1")).thenReturn(10);
        when(stockFeignSupport.getAvailable("p2", "trusted-hash-2")).thenReturn(10);
        when(redisComponent.getLogisticsInfo()).thenReturn(sender());

        ReflectionTestUtils.invokeMethod(service, "createOrder", "user-1", request);

        verify(stockFeignSupport, never()).getAvailable("p1", "forged-hash-1");
        verify(stockFeignSupport, never()).getAvailable("p2", "forged-hash-2");
        assertEquals("trusted-hash-1", first.getPropertyValueIdHash());
        assertEquals("trusted-hash-2", second.getPropertyValueIdHash());

        ArgumentCaptor<List<ProductItem>> stockCaptor = listCaptor();
        verify(stockFeignSupport).lockAndVerify(stockCaptor.capture());
        assertEquals(Set.of("trusted-hash-1", "trusted-hash-2"), hashes(stockCaptor.getValue()));
        verify(stockFeignSupport).changeStockBatch(stockCaptor.capture());
        assertEquals(Set.of("trusted-hash-1", "trusted-hash-2"), hashes(stockCaptor.getValue()));

        ArgumentCaptor<List<OrderInfo>> orderCaptor = listCaptor();
        ArgumentCaptor<List<OrderItem>> itemCaptor = listCaptor();
        ArgumentCaptor<List<OrderLogisticsInfo>> logisticsCaptor = listCaptor();
        verify(orderInfoMapper).insertBatch(orderCaptor.capture());
        verify(orderItemMapper).insertBatch(itemCaptor.capture());
        verify(orderLogisticsInfoMapper).insertBatch(logisticsCaptor.capture());

        List<OrderInfo> orders = orderCaptor.getValue();
        List<OrderLogisticsInfo> logistics = logisticsCaptor.getValue();
        assertEquals(2, orders.size());
        assertEquals(2, logistics.size());
        assertEquals(
                orders.stream().map(OrderInfo::getOrderId).collect(Collectors.toSet()),
                logistics.stream().map(OrderLogisticsInfo::getOrderId).collect(Collectors.toSet()));
        assertEquals(Set.of(OrderStatusEnum.WAIT_PAYMENT.getStatus()),
                orders.stream().map(OrderInfo::getOrderStatus).collect(Collectors.toSet()));
        assertEquals(Set.of(LogisticsStatusEnum.PENDING_SHIPMENT.getStatus()),
                logistics.stream().map(OrderLogisticsInfo::getLogisticsStatus).collect(Collectors.toSet()));
        assertEquals(Set.of("Shanghai"),
                logistics.stream().map(OrderLogisticsInfo::getReceiverAddress).collect(Collectors.toSet()));
        assertEquals(Set.of("Warehouse"),
                logistics.stream().map(OrderLogisticsInfo::getSenderAddress).collect(Collectors.toSet()));
        assertEquals(Set.of("trusted-hash-1", "trusted-hash-2"),
                itemCaptor.getValue().stream()
                        .map(OrderItem::getPropertyValueIdHash)
                        .collect(Collectors.toSet()));
    }

    private static PostOrderDTO request(List<ProductItem> items) {
        PostOrderDTO request = new PostOrderDTO();
        request.setPayMethod("alipay_wap");
        request.setAddressId("address-1");
        request.setOrderFrom(OrderFromTypeEnum.PRODUCT.getType());
        request.setOrderList(items);
        return request;
    }

    private static ProductItem item(String productId, String propertyValueIds, String forgedHash) {
        ProductItem item = new ProductItem();
        item.setProductId(productId);
        item.setPropertyValueIds(propertyValueIds);
        item.setPropertyValueIdHash(forgedHash);
        item.setBuyCount(1);
        return item;
    }

    private static UserAddressVO address() {
        UserAddressVO address = new UserAddressVO();
        address.setAddress("Shanghai");
        address.setAddressee("Demo User");
        address.setPhone("13800000000");
        return address;
    }

    private static LogisticsSendDTO sender() {
        LogisticsSendDTO sender = new LogisticsSendDTO();
        sender.setSenderName("AI Shop");
        sender.setSenderPhone("021-12345678");
        sender.setSenderAddress("Warehouse");
        return sender;
    }

    private static ProductInfoSnapshotVO product(String productId, String name) {
        ProductInfoSnapshotVO product = new ProductInfoSnapshotVO();
        product.setProductId(productId);
        product.setProductName(name);
        product.setStatus(ProductStatusEnum.ON_SALE.getStatus());
        return product;
    }

    private static ProductPropertyValueSnapshotVO property(String productId, String valueId) {
        ProductPropertyValueSnapshotVO property = new ProductPropertyValueSnapshotVO();
        property.setProductId(productId);
        property.setPropertyValueId(valueId);
        property.setPropertyName("Style");
        property.setPropertyValue(valueId);
        return property;
    }

    private static ProductSkuSnapshotVO sku(
            String productId, String propertyValueIds, String hash, String price) {
        ProductSkuSnapshotVO sku = new ProductSkuSnapshotVO();
        sku.setProductId(productId);
        sku.setPropertyValueIds(propertyValueIds);
        sku.setPropertyValueIdHash(hash);
        sku.setPrice(new BigDecimal(price));
        return sku;
    }

    private static Set<String> hashes(List<ProductItem> items) {
        return items.stream().map(ProductItem::getPropertyValueIdHash).collect(Collectors.toSet());
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private static <T> ArgumentCaptor<List<T>> listCaptor() {
        return (ArgumentCaptor) ArgumentCaptor.forClass(List.class);
    }
}
