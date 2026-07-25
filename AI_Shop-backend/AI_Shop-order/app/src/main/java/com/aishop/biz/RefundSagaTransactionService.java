package com.aishop.biz;

import com.aishop.api.dto.RefundStockRestoreDTO;
import com.aishop.api.enums.OrderItemStatusEnum;
import com.aishop.api.enums.OrderStatusEnum;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.TransactionalMqSender;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.enums.RefundSagaStatus;
import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.po.OrderItem;
import com.aishop.entity.po.RefundRequest;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.OrderInfoMapper;
import com.aishop.mappers.OrderItemMapper;
import com.aishop.mappers.RefundRequestMapper;
import com.aishop.support.MqIdempotencyKeys;
import jakarta.annotation.Resource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.util.Date;
import java.util.List;
import java.util.UUID;

@Service
public class RefundSagaTransactionService {

    @Resource
    private RefundRequestMapper refundRequestMapper;
    @Resource
    private OrderInfoMapper<OrderInfo, ?> orderInfoMapper;
    @Resource
    private OrderItemMapper<OrderItem, ?> orderItemMapper;
    @Resource
    private TransactionalMqSender transactionalMqSender;

    @Value("${refund.saga.retry-seconds:60}")
    private int retrySeconds;
    @Value("${refund.saga.max-retries:6}")
    private int maxRetries;

    @Transactional(rollbackFor = Exception.class)
    public RefundRequest createOrLoad(String orderItemId, String userId) {
        RefundRequest existing = refundRequestMapper.selectByOrderItemId(orderItemId);
        if (existing != null) {
            assertOwner(existing, userId);
            return existing;
        }

        OrderItem item = orderItemMapper.selectByOrderItemIdForUpdate(orderItemId);
        if (item == null) {
            throw new BusinessException("订单明细不存在");
        }
        OrderInfo order = orderInfoMapper.selectByOrderIdForUpdate(item.getOrderId());
        if (order == null || !userId.equals(order.getUserId())) {
            throw new BusinessException("订单不存在");
        }
        validateRefundable(order, item);

        String requestId = stableRefundId(orderItemId);
        RefundRequest request = new RefundRequest();
        request.setRefundRequestId(requestId);
        request.setRefundOrderNo(requestId);
        request.setSourcePayOrderId(order.getPayOrderId());
        request.setOrderId(order.getOrderId());
        request.setOrderItemId(item.getOrderItemId());
        request.setUserId(userId);
        request.setProductId(item.getProductId());
        request.setPropertyValueIdHash(item.getPropertyValueIdHash());
        request.setBuyCount(item.getBuyCount());
        request.setRefundAmount(item.getItemAmount());
        request.setPayChannel(order.getPayChannel());
        request.setStatus(RefundSagaStatus.PENDING_PAYMENT.name());
        request.setRetryCount(0);
        request.setCreatedAt(new Date());
        request.setUpdatedAt(new Date());
        refundRequestMapper.insertIgnore(request);

        RefundRequest stored = refundRequestMapper.selectByOrderItemId(orderItemId);
        if (stored == null) {
            throw new BusinessException("退款请求创建失败");
        }
        assertOwner(stored, userId);
        return stored;
    }

    public RefundRequest get(String refundRequestId) {
        return refundRequestMapper.selectById(refundRequestId);
    }

    public List<RefundRequest> selectDue(int limit) {
        return refundRequestMapper.selectDue(Math.max(1, Math.min(limit, 100)));
    }

    @Transactional(rollbackFor = Exception.class)
    public boolean claimPaymentAttempt(String refundRequestId) {
        return refundRequestMapper.claimPaymentAttempt(refundRequestId, retrySeconds) == 1;
    }

    @Transactional(rollbackFor = Exception.class)
    public void markPaymentConfirmed(String refundRequestId) {
        refundRequestMapper.markPaymentConfirmed(refundRequestId);
    }

    @Transactional(rollbackFor = Exception.class)
    public void recordPaymentFailure(String refundRequestId, String error) {
        refundRequestMapper.recordPaymentFailure(
                refundRequestId, retrySeconds, maxRetries, truncate(error));
    }

    @Transactional(rollbackFor = Exception.class)
    public boolean queueStockRestore(String refundRequestId, boolean retry) {
        RefundRequest request = refundRequestMapper.selectByIdForUpdate(refundRequestId);
        if (request == null) {
            return false;
        }
        RefundSagaStatus status = RefundSagaStatus.valueOf(request.getStatus());
        if (status == RefundSagaStatus.COMPLETED || status == RefundSagaStatus.MANUAL_REVIEW) {
            return false;
        }

        int attempt = 0;
        if (status == RefundSagaStatus.PAYMENT_CONFIRMED) {
            finalizeOrderRefund(request);
            refundRequestMapper.markStockPending(refundRequestId, retrySeconds);
        } else if (status == RefundSagaStatus.STOCK_PENDING && retry) {
            if (request.getRetryCount() != null && request.getRetryCount() >= maxRetries) {
                refundRequestMapper.recordStockRetry(
                        refundRequestId, retrySeconds, maxRetries, "库存恢复重试已耗尽");
                return false;
            }
            refundRequestMapper.recordStockRetry(
                    refundRequestId, retrySeconds, maxRetries, "等待库存恢复确认");
            RefundRequest updated = refundRequestMapper.selectByIdForUpdate(refundRequestId);
            if (updated == null || RefundSagaStatus.MANUAL_REVIEW.name().equals(updated.getStatus())) {
                return false;
            }
            attempt = updated.getRetryCount() == null ? 1 : updated.getRetryCount();
        } else {
            return false;
        }

        RefundStockRestoreDTO payload = new RefundStockRestoreDTO();
        payload.setRefundRequestId(request.getRefundRequestId());
        payload.setBusinessKey(request.getRefundRequestId());
        payload.setProductId(request.getProductId());
        payload.setPropertyValueIdHash(request.getPropertyValueIdHash());
        payload.setChangeAmount(request.getBuyCount());
        transactionalMqSender.sendAfterCommit(
                RabbitMQConfig.REFUND_EXCHANGE,
                RabbitMQConfig.REFUND_STOCK_KEY,
                payload,
                MqIdempotencyKeys.refundStock(request.getRefundRequestId(), attempt),
                MessageReliabilityLevelEnum.STANDARD);
        return true;
    }

    @Transactional(rollbackFor = Exception.class)
    public void markCompleted(String refundRequestId) {
        refundRequestMapper.markCompleted(refundRequestId);
    }

    private void finalizeOrderRefund(RefundRequest request) {
        OrderItem item = orderItemMapper.selectByOrderItemIdForUpdate(request.getOrderItemId());
        OrderInfo order = orderInfoMapper.selectByOrderIdForUpdate(request.getOrderId());
        if (item == null || order == null) {
            throw new BusinessException("退款订单数据不存在");
        }
        if (!OrderItemStatusEnum.REFUND.getStatus().equals(item.getOrderItemStatus())) {
            item.setOrderItemStatus(OrderItemStatusEnum.REFUND.getStatus());
            item.setRefundOrderId(request.getRefundOrderNo());
            orderItemMapper.updateByOrderItemId(item, item.getOrderItemId());
        }

        Integer normalCount = orderItemMapper.countNormalByOrderId(order.getOrderId());
        order.setOrderStatus(normalCount == null || normalCount == 0
                ? OrderStatusEnum.REFUNDED.getStatus()
                : OrderStatusEnum.PARTIALLY_REFUNDED.getStatus());
        orderInfoMapper.updateByOrderId(order, order.getOrderId());
    }

    private static void validateRefundable(OrderInfo order, OrderItem item) {
        Integer status = order.getOrderStatus();
        if (!OrderStatusEnum.PAID.getStatus().equals(status)
                && !OrderStatusEnum.SHIPPED.getStatus().equals(status)
                && !OrderStatusEnum.PARTIALLY_REFUNDED.getStatus().equals(status)) {
            throw new BusinessException("当前订单状态不能申请退款");
        }
        if (!OrderItemStatusEnum.NORMAL.getStatus().equals(item.getOrderItemStatus())) {
            throw new BusinessException("当前订单项状态不能申请退款");
        }
        if (item.getItemAmount() == null || item.getItemAmount().signum() <= 0) {
            throw new BusinessException("退款金额必须大于0");
        }
        if (item.getBuyCount() == null || item.getBuyCount() <= 0) {
            throw new BusinessException("退款商品数量异常");
        }
        if (order.getPayOrderId() == null || order.getPayOrderId().isBlank()) {
            throw new BusinessException("支付流水不存在");
        }
    }

    private static String stableRefundId(String orderItemId) {
        return UUID.nameUUIDFromBytes(
                ("refund:" + orderItemId).getBytes(StandardCharsets.UTF_8))
                .toString()
                .replace("-", "");
    }

    private static void assertOwner(RefundRequest request, String userId) {
        if (!userId.equals(request.getUserId())) {
            throw new BusinessException("退款请求不存在");
        }
    }

    private static String truncate(String error) {
        if (error == null) {
            return null;
        }
        return error.length() > 500 ? error.substring(0, 500) : error;
    }
}
