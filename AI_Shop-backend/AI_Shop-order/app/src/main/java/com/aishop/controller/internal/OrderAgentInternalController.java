package com.aishop.controller.internal;

import com.aishop.biz.OrderCommentService;
import com.aishop.biz.OrderInfoService;
import com.aishop.biz.OrderItemService;
import com.aishop.biz.OrderLogisticsInfoService;
import com.aishop.controller.ABaseController;
import com.aishop.api.enums.OrderStatusEnum;
import com.aishop.entity.po.OrderComment;
import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.po.OrderItem;
import com.aishop.entity.po.OrderLogisticsInfo;
import com.aishop.entity.query.OrderCommentQuery;
import com.aishop.entity.query.OrderInfoQuery;
import com.aishop.entity.query.OrderItemQuery;
import com.aishop.entity.query.SimplePage;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/internal/order/agent")
public class OrderAgentInternalController extends ABaseController {

    private static final DateTimeFormatter DT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    @Resource
    private OrderInfoService orderInfoService;
    @Resource
    private OrderItemService orderItemService;
    @Resource
    private OrderLogisticsInfoService orderLogisticsInfoService;
    @Resource
    private OrderCommentService orderCommentService;

    @PostMapping("/listOrders")
    public ResponseVO<List<Map<String, Object>>> listOrders(@RequestBody Map<String, Object> body) {
        String userId = str(body, "userId");
        if (StringTools.isEmpty(userId)) {
            return getSuccessResponseVO(Collections.emptyList());
        }
        OrderInfoQuery query = new OrderInfoQuery();
        query.setUserId(userId);
        query.setQueryItems(true);
        query.setOrderBy(com.aishop.entity.query.SafeSort.of("o.order_time desc"));
        String orderId = str(body, "orderId");
        if (!StringTools.isEmpty(orderId)) {
            query.setOrderId(orderId);
        }
        query.setOrderStatusList(new Integer[]{
                OrderStatusEnum.WAIT_PAYMENT.getStatus(),
                OrderStatusEnum.PAID.getStatus(),
                OrderStatusEnum.SHIPPED.getStatus(),
                OrderStatusEnum.COMPLETED.getStatus(),
                OrderStatusEnum.CANCELLED.getStatus(),
                OrderStatusEnum.CLOSED.getStatus(),
                OrderStatusEnum.REFUNDED.getStatus(),
                OrderStatusEnum.PARTIALLY_REFUNDED.getStatus()
        });
        int limit = intVal(body.get("limit"), 30);
        query.setSimplePage(new SimplePage(0, limit));
        // time range: filter in memory after query if needed (mapper may lack timeStart)
        List<OrderInfo> list = orderInfoService.findListByParam(query);
        String timeStart = str(body, "timeStart");
        String timeEnd = str(body, "timeEnd");
        List<Map<String, Object>> result = new ArrayList<>();
        for (OrderInfo o : list) {
            if (!inTimeRange(o.getOrderTime(), timeStart, timeEnd)) {
                continue;
            }
            result.add(toOrderMap(o, true));
        }
        return getSuccessResponseVO(result);
    }

    @PostMapping("/getOrder")
    public ResponseVO<Map<String, Object>> getOrder(@RequestBody Map<String, Object> body) {
        String orderId = str(body, "orderId");
        if (StringTools.isEmpty(orderId)) {
            return getSuccessResponseVO(null);
        }
        OrderInfo order = orderInfoService.getOrderInfoByOrderId(orderId);
        if (order == null) {
            return getSuccessResponseVO(null);
        }
        OrderItemQuery iq = new OrderItemQuery();
        iq.setOrderId(orderId);
        order.setOrderItemList(orderItemService.findListByParam(iq));
        return getSuccessResponseVO(toOrderMap(order, true));
    }

    @PostMapping("/getOrderItem")
    public ResponseVO<Map<String, Object>> getOrderItem(@RequestBody Map<String, Object> body) {
        String orderItemId = str(body, "orderItemId");
        if (StringTools.isEmpty(orderItemId)) {
            return getSuccessResponseVO(null);
        }
        OrderItem item = orderItemService.getOrderItemByOrderItemId(orderItemId);
        return getSuccessResponseVO(item == null ? null : toItemMap(item));
    }

    @PostMapping("/listOrderItems")
    public ResponseVO<List<Map<String, Object>>> listOrderItems(@RequestBody Map<String, Object> body) {
        String orderId = str(body, "orderId");
        if (StringTools.isEmpty(orderId)) {
            return getSuccessResponseVO(Collections.emptyList());
        }
        OrderItemQuery iq = new OrderItemQuery();
        iq.setOrderId(orderId);
        iq.setOrderBy(com.aishop.entity.query.SafeSort.of("order_item_id asc"));
        List<OrderItem> items = orderItemService.findListByParam(iq);
        List<Map<String, Object>> result = new ArrayList<>();
        if (items != null) {
            for (OrderItem item : items) {
                result.add(toItemMap(item));
            }
        }
        return getSuccessResponseVO(result);
    }

    @PostMapping("/getLogistics")
    public ResponseVO<Map<String, Object>> getLogistics(@RequestBody Map<String, Object> body) {
        String userId = str(body, "userId");
        String orderId = str(body, "orderId");
        if (StringTools.isEmpty(userId) || StringTools.isEmpty(orderId)) {
            return getSuccessResponseVO(null);
        }
        OrderLogisticsInfo info;
        try {
            info = orderLogisticsInfoService.getOrderLogisticsRecords(userId, orderId);
        } catch (Exception e) {
            return getSuccessResponseVO(null);
        }
        if (info == null) {
            return getSuccessResponseVO(null);
        }
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("orderId", info.getOrderId());
        map.put("userId", info.getUserId());
        map.put("logisticsNo", info.getLogisticsNo());
        map.put("logisticsCompany", info.getLogisticsCompany());
        map.put("logisticsStatus", info.getLogisticsStatus());
        map.put("receiverName", info.getReceiverName());
        map.put("receiverPhone", info.getReceiverPhone());
        map.put("receiverAddress", info.getReceiverAddress());
        map.put("recordList", info.getRecordList());
        return getSuccessResponseVO(map);
    }

    @PostMapping("/getComment")
    public ResponseVO<Map<String, Object>> getComment(@RequestBody Map<String, Object> body) {
        String userId = str(body, "userId");
        String orderId = str(body, "orderId");
        if (StringTools.isEmpty(userId) || StringTools.isEmpty(orderId)) {
            return getSuccessResponseVO(null);
        }
        OrderCommentQuery q = new OrderCommentQuery();
        q.setUserId(userId);
        q.setOrderId(orderId);
        List<OrderComment> list = orderCommentService.findListByParam(q);
        if (list == null || list.isEmpty()) {
            return getSuccessResponseVO(null);
        }
        OrderComment c = list.get(0);
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("orderId", c.getOrderId());
        map.put("productId", c.getProductId());
        map.put("userId", c.getUserId());
        map.put("commentContent", c.getCommentContent());
        map.put("star", c.getStar());
        map.put("commentTime", formatDate(c.getCommentTime()));
        map.put("commentImages", c.getCommentImages());
        map.put("commentBizReply", c.getCommentBizReply());
        map.put("recommentContent", c.getRecommentContent());
        return getSuccessResponseVO(map);
    }

    @PostMapping("/coPurchaseProductIds")
    public ResponseVO<List<String>> coPurchaseProductIds(@RequestBody Map<String, Object> body) {
        String productId = str(body, "productId");
        if (StringTools.isEmpty(productId)) {
            return getSuccessResponseVO(Collections.emptyList());
        }
        int limit = Math.min(Math.max(intVal(body.get("limit"), 5), 1), 20);

        // Step 1: find order IDs that contain the seed product (cap at 30 orders).
        OrderItemQuery seedQuery = new OrderItemQuery();
        seedQuery.setProductId(productId);
        seedQuery.setSimplePage(new SimplePage(0, 30));
        List<OrderItem> seedItems = orderItemService.findListByParam(seedQuery);
        if (seedItems == null || seedItems.isEmpty()) {
            return getSuccessResponseVO(Collections.emptyList());
        }
        Set<String> orderIds = new LinkedHashSet<>();
        for (OrderItem item : seedItems) {
            orderIds.add(item.getOrderId());
        }

        // Step 2: across those orders, count how often each other product appears.
        Map<String, Integer> freq = new LinkedHashMap<>();
        for (String orderId : orderIds) {
            OrderItemQuery oiq = new OrderItemQuery();
            oiq.setOrderId(orderId);
            List<OrderItem> items = orderItemService.findListByParam(oiq);
            if (items == null) continue;
            for (OrderItem item : items) {
                String pid = item.getProductId();
                if (StringTools.isEmpty(pid) || productId.equals(pid)) continue;
                freq.merge(pid, 1, Integer::sum);
            }
        }
        if (freq.isEmpty()) {
            return getSuccessResponseVO(Collections.emptyList());
        }

        // Step 3: sort by co-purchase frequency descending, return top limit.
        List<String> result = freq.entrySet().stream()
            .sorted(Map.Entry.<String, Integer>comparingByValue().reversed())
            .limit(limit)
            .map(Map.Entry::getKey)
            .collect(Collectors.toList());
        return getSuccessResponseVO(result);
    }

    private Map<String, Object> toOrderMap(OrderInfo o, boolean withItems) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("orderId", o.getOrderId());
        m.put("userId", o.getUserId());
        m.put("orderStatus", o.getOrderStatus());
        m.put("amount", o.getAmount());
        m.put("payScene", o.getPayScene());
        m.put("payChannel", o.getPayChannel());
        m.put("payOrderId", o.getPayOrderId());
        m.put("orderTime", formatDate(o.getOrderTime()));
        m.put("subject", o.getSubject());
        m.put("commentStatus", o.getCommentStatus());
        if (withItems) {
            List<Map<String, Object>> items = new ArrayList<>();
            if (o.getOrderItemList() != null) {
                for (OrderItem item : o.getOrderItemList()) {
                    items.add(toItemMap(item));
                }
            }
            m.put("items", items);
        }
        return m;
    }

    private Map<String, Object> toItemMap(OrderItem item) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("orderId", item.getOrderId());
        m.put("orderItemId", item.getOrderItemId());
        m.put("productId", item.getProductId());
        m.put("productName", item.getProductName());
        m.put("cover", item.getCover());
        m.put("propertyInfo", item.getPropertyInfo());
        m.put("propertyValueIdHash", item.getPropertyValueIdHash());
        m.put("itemAmount", item.getItemAmount());
        m.put("buyCount", item.getBuyCount());
        m.put("orderItemStatus", item.getOrderItemStatus());
        return m;
    }

    private static boolean inTimeRange(Date orderTime, String timeStart, String timeEnd) {
        if (orderTime == null) {
            return true;
        }
        if (StringTools.isEmpty(timeStart) && StringTools.isEmpty(timeEnd)) {
            return true;
        }
        LocalDateTime t = LocalDateTime.ofInstant(orderTime.toInstant(), ZoneId.systemDefault());
        if (!StringTools.isEmpty(timeStart)) {
            LocalDateTime start = parseDt(timeStart, true);
            if (start != null && t.isBefore(start)) {
                return false;
            }
        }
        if (!StringTools.isEmpty(timeEnd)) {
            LocalDateTime end = parseDt(timeEnd, false);
            if (end != null && t.isAfter(end)) {
                return false;
            }
        }
        return true;
    }

    private static LocalDateTime parseDt(String s, boolean startOfDay) {
        try {
            String n = normalizeDt(s);
            if (n.length() == 10) {
                n = n + (startOfDay ? " 00:00:00" : " 23:59:59");
            }
            return LocalDateTime.parse(n, DT);
        } catch (Exception e) {
            return null;
        }
    }

    private static String normalizeDt(String s) {
        if (s == null) {
            return null;
        }
        return s.contains("T") ? s.replace('T', ' ') : s;
    }

    private static String formatDate(Date d) {
        if (d == null) {
            return null;
        }
        return DT.format(LocalDateTime.ofInstant(d.toInstant(), ZoneId.systemDefault()));
    }

    private static String str(Map<String, Object> body, String key) {
        if (body == null || body.get(key) == null) {
            return null;
        }
        return String.valueOf(body.get(key));
    }

    private static int intVal(Object v, int def) {
        if (v == null) {
            return def;
        }
        try {
            return Integer.parseInt(String.valueOf(v));
        } catch (Exception e) {
            return def;
        }
    }
}
