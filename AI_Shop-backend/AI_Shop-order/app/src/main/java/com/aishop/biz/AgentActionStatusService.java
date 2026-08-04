package com.aishop.biz;

import com.aishop.api.enums.OrderItemStatusEnum;
import com.aishop.api.enums.OrderStatusEnum;
import com.aishop.entity.po.OrderComment;
import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.po.OrderItem;
import com.aishop.entity.po.OrderRequestIdempotency;
import com.aishop.entity.po.RefundRequest;
import com.aishop.utils.JsonUtils;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Service;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Reconciles Agent write commands after an HTTP response is lost.
 *
 * <p>The idempotency ledger is authoritative when it contains a completed
 * response. A PROCESSING or legacy request is additionally checked against the
 * order domain state so a command that committed just before a process/network
 * failure can still be closed as successful.</p>
 */
@Service
public class AgentActionStatusService {

    public static final String STATUS_SUCCESS = "SUCCESS";
    public static final String STATUS_FAILED = "FAILED";
    public static final String STATUS_PROCESSING = "PROCESSING";
    public static final String STATUS_UNKNOWN = "UNKNOWN";

    @Resource
    private OrderRequestIdempotencyService idempotencyService;
    @Resource
    private OrderInfoService orderInfoService;
    @Resource
    private OrderItemService orderItemService;
    @Resource
    private OrderCommentService orderCommentService;
    @Resource
    private RefundSagaTransactionService refundSagaTransactionService;

    public Map<String, Object> resolve(Map<String, Object> body) {
        String userId = stringValue(body.get("userId"));
        String actionType = stringValue(body.get("actionType"));
        String idempotencyKey = stringValue(body.get("idempotencyKey"));
        String commandType = commandType(actionType);
        if (StringTools.isEmpty(userId)
                || StringTools.isEmpty(idempotencyKey)
                || commandType == null) {
            return result(STATUS_UNKNOWN, "无法识别待核对操作");
        }

        OrderRequestIdempotency ledger = idempotencyService.find(
                userId, commandType, idempotencyKey);
        if (ledger != null && "COMPLETED".equals(ledger.getStatus())) {
            return result(STATUS_SUCCESS, successMessage(actionType));
        }
        Map<String, Object> params = params(body.get("params"));
        if (effectObserved(userId, actionType, params)) {
            if (ledger != null && ("PROCESSING".equals(ledger.getStatus())
                    || "FAILED".equals(ledger.getStatus()))) {
                idempotencyService.markReconciled(
                        userId,
                        commandType,
                        idempotencyKey,
                        successMessage(actionType));
            }
            return result(STATUS_SUCCESS, successMessage(actionType));
        }
        if (ledger != null && "FAILED".equals(ledger.getStatus())) {
            return result(STATUS_FAILED, failureMessage(ledger));
        }
        if (ledger != null) {
            return result(STATUS_PROCESSING, "操作仍在处理中");
        }
        return result(STATUS_UNKNOWN, "未找到可确认的执行记录");
    }

    public static String commandType(String actionType) {
        return switch (actionType) {
            case "REFUND" -> OrderRequestIdempotencyService.COMMAND_AGENT_REFUND;
            case "CONFIRM_RECEIPT" ->
                    OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT;
            case "PRODUCT_REVIEW" ->
                    OrderRequestIdempotencyService.COMMAND_AGENT_PRODUCT_REVIEW;
            case "RECOMMENT" -> OrderRequestIdempotencyService.COMMAND_AGENT_RECOMMENT;
            default -> null;
        };
    }

    private boolean effectObserved(
            String userId, String actionType, Map<String, Object> params) {
        return switch (actionType) {
            case "REFUND" -> refundObserved(userId, stringValue(params.get("orderItemId")));
            case "CONFIRM_RECEIPT" ->
                    receiptObserved(userId, stringValue(params.get("orderId")));
            case "PRODUCT_REVIEW" ->
                    reviewObserved(userId, stringValue(params.get("orderId")), false);
            case "RECOMMENT" ->
                    reviewObserved(userId, stringValue(params.get("orderId")), true);
            default -> false;
        };
    }

    private boolean refundObserved(String userId, String orderItemId) {
        if (StringTools.isEmpty(orderItemId)) {
            return false;
        }
        RefundRequest request = refundSagaTransactionService.findByOrderItemId(orderItemId);
        if (request != null) {
            return userId.equals(request.getUserId());
        }
        OrderItem item = orderItemService.getOrderItemByOrderItemId(orderItemId);
        if (item == null
                || !OrderItemStatusEnum.REFUND.getStatus().equals(item.getOrderItemStatus())) {
            return false;
        }
        OrderInfo order = orderInfoService.getOrderInfoByOrderId(item.getOrderId());
        return order != null && userId.equals(order.getUserId());
    }

    private boolean receiptObserved(String userId, String orderId) {
        if (StringTools.isEmpty(orderId)) {
            return false;
        }
        OrderInfo order = orderInfoService.getOrderInfoByOrderId(orderId);
        return order != null
                && userId.equals(order.getUserId())
                && OrderStatusEnum.COMPLETED.getStatus().equals(order.getOrderStatus());
    }

    private boolean reviewObserved(String userId, String orderId, boolean recomment) {
        if (StringTools.isEmpty(orderId)) {
            return false;
        }
        OrderComment comment = orderCommentService.getOrderCommentByOrderId(orderId);
        if (comment == null || !userId.equals(comment.getUserId())) {
            return false;
        }
        String content = recomment
                ? comment.getRecommentContent()
                : comment.getCommentContent();
        return !StringTools.isEmpty(content);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> params(Object raw) {
        if (!(raw instanceof Map<?, ?> map)) {
            return Collections.emptyMap();
        }
        Map<String, Object> result = new LinkedHashMap<>();
        map.forEach((key, value) -> result.put(String.valueOf(key), value));
        return result;
    }

    private static Map<String, Object> result(String status, String message) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", status);
        result.put("resultMessage", message);
        return result;
    }

    private static String successMessage(String actionType) {
        return switch (actionType) {
            case "REFUND" -> "退款操作已受理";
            case "CONFIRM_RECEIPT" -> "订单已确认收货";
            case "PRODUCT_REVIEW" -> "订单评价已提交";
            case "RECOMMENT" -> "订单追评已提交";
            default -> "操作已完成";
        };
    }

    private static String failureMessage(OrderRequestIdempotency ledger) {
        String response = ledger.getResponseJson();
        if (response != null && !response.isBlank()) {
            try {
                Map<?, ?> payload = JsonUtils.parseObject(response, Map.class);
                Object message = payload == null ? null : payload.get("errorMessage");
                if (message != null && !String.valueOf(message).isBlank()) {
                    return String.valueOf(message);
                }
            } catch (RuntimeException ignored) {
                // Malformed legacy rows should not break the status endpoint.
            }
        }
        return "操作执行失败";
    }

    private static String stringValue(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }
}
