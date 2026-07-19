package com.simlect.biz;

import com.simlect.api.dto.CouponRushPayRequestDTO;
import com.simlect.api.dto.CouponRushPrepareRequestDTO;
import com.simlect.api.dto.OrderIdDTO;
import com.simlect.api.dto.OrderStatsRangeDTO;
import com.simlect.api.dto.UserIdDTO;
import com.simlect.api.vo.OrderBriefVO;
import com.simlect.api.vo.OrderDailyStatsVO;
import com.simlect.api.vo.OrderRangeStatsVO;
import com.simlect.api.dto.CouponRushPrepareDTO;
import com.simlect.api.dto.PayInfoDTO;
import com.simlect.api.dto.PayOrderNotifyDTO;
import com.simlect.entity.enums.DateTimePatternEnum;
import com.simlect.api.enums.OrderItemStatusEnum;
import com.simlect.api.enums.OrderStatusEnum;
import com.simlect.entity.po.OrderInfo;
import com.simlect.entity.po.OrderItem;
import com.simlect.entity.query.OrderInfoQuery;
import com.simlect.utils.DateUtil;
import com.simlect.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

@Service
public class OrderInternalService {

    private static final Integer[] SALE_STATUSES = new Integer[]{
            OrderStatusEnum.PAID.getStatus(),
            OrderStatusEnum.SHIPPED.getStatus(),
            OrderStatusEnum.COMPLETED.getStatus(),
            OrderStatusEnum.PARTIALLY_REFUNDED.getStatus()
    };

    private static final Integer[] REFUND_STATUSES = new Integer[]{
            OrderStatusEnum.REFUNDED.getStatus(),
            OrderStatusEnum.PARTIALLY_REFUNDED.getStatus()
    };

    @Resource
    private OrderInfoService orderInfoService;

    public OrderBriefVO getOrder(OrderIdDTO dto) {
        if (dto == null || StringTools.isEmpty(dto.getOrderId())) {
            return null;
        }
        OrderInfo order = orderInfoService.getOrderInfoByOrderId(dto.getOrderId());
        if (order == null) {
            return null;
        }
        OrderBriefVO vo = new OrderBriefVO();
        vo.setOrderId(order.getOrderId());
        vo.setUserId(order.getUserId());
        vo.setOrderStatus(order.getOrderStatus());
        vo.setAmount(order.getAmount());
        return vo;
    }

    public boolean cancelUnpaidForPayTimeout(OrderIdDTO dto) {
        return orderInfoService.cancelUnpaidOrderForPayTimeout(dto.getOrderId());
    }

    public boolean confirmReceipt(OrderIdDTO dto) {
        return orderInfoService.confirmOrderReceipt(dto.getUserId(), dto.getOrderId());
    }

    public void onConfirmed(OrderIdDTO dto) {
        orderInfoService.onOrderConfirmed(dto.getUserId(), dto.getOrderId());
    }

    public void paySuccess(PayOrderNotifyDTO dto) {
        orderInfoService.paySuccess(dto);
    }

    public CouponRushPrepareDTO prepareCouponRush(CouponRushPrepareRequestDTO dto) {
        return orderInfoService.prepareCouponRush(dto.getUserId(), dto.getCouponId());
    }

    public PayInfoDTO postCouponRushOrder(CouponRushPayRequestDTO dto) {
        return orderInfoService.postCouponRushOrder(dto.getUserId(), dto.getCouponId(), dto.getPayMethod());
    }

    public void syncPaidCouponRushUserCoupons(UserIdDTO dto) {
        orderInfoService.syncPaidCouponRushUserCoupons(dto.getUserId());
    }

    public void cancelOrder(OrderIdDTO dto) {
        orderInfoService.cancelOrder(dto.getUserId(), dto.getOrderId(), OrderStatusEnum.WAIT_PAYMENT);
    }

    public OrderRangeStatsVO aggregateRange(OrderStatsRangeDTO dto) {
        OrderRangeStatsVO vo = new OrderRangeStatsVO();
        if (dto == null || StringTools.isEmpty(dto.getStartTime()) || StringTools.isEmpty(dto.getEndTime())) {
            return vo;
        }
        OrderInfoQuery saleQuery = baseQuery(dto.getStartTime(), dto.getEndTime());
        saleQuery.setOrderStatusList(SALE_STATUSES);
        for (OrderInfo order : safeList(orderInfoService.findListByParam(saleQuery))) {
            BigDecimal saleAmount = calcEffectiveSaleAmount(order);
            if (saleAmount.compareTo(BigDecimal.ZERO) > 0) {
                vo.setSaleAmount(vo.getSaleAmount().add(saleAmount));
                vo.setSaleOrderCount(vo.getSaleOrderCount().add(BigDecimal.ONE));
            }
        }

        OrderInfoQuery refundQuery = baseQuery(dto.getStartTime(), dto.getEndTime());
        refundQuery.setOrderStatusList(REFUND_STATUSES);
        BigDecimal refundAmount = BigDecimal.ZERO;
        for (OrderInfo order : safeList(orderInfoService.findListByParam(refundQuery))) {
            refundAmount = refundAmount.add(sumNonNormalItemAmount(order));
        }
        vo.setRefundAmount(refundAmount);
        return vo;
    }

    public List<OrderDailyStatsVO> aggregateDaily(OrderStatsRangeDTO dto) {
        if (dto == null || StringTools.isEmpty(dto.getStartTime()) || StringTools.isEmpty(dto.getEndTime())) {
            return Collections.emptyList();
        }
        OrderInfoQuery query = baseQuery(dto.getStartTime(), dto.getEndTime());
        query.setOrderBy("order_time asc");
        List<OrderInfo> orderInfoList = safeList(orderInfoService.findListByParam(query));
        if (orderInfoList.isEmpty()) {
            return Collections.emptyList();
        }

        List<OrderDailyStatsVO> result = new ArrayList<>();
        OrderInfo first = orderInfoList.get(0);
        String pDate = DateUtil.format(first.getOrderTime(), DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern());
        BigDecimal valueSaleAmount = BigDecimal.ZERO;
        BigDecimal valueSaleCount = BigDecimal.ZERO;
        BigDecimal valueRefundAmount = BigDecimal.ZERO;
        BigDecimal valueRefundCount = BigDecimal.ZERO;

        for (OrderInfo orderInfo : orderInfoList) {
            if (StringTools.isEmpty(orderInfo.getChannelOrderId())) {
                continue;
            }
            String tempDate = DateUtil.format(orderInfo.getOrderTime(), DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern());
            if (Boolean.TRUE.equals(DateUtil.isAfterOneAM(tempDate))
                    && Boolean.TRUE.equals(DateUtil.isDayDifferenceAtLeastOne(tempDate, pDate))) {
                result.add(toDailyVo(pDate, valueSaleAmount, valueSaleCount, valueRefundAmount, valueRefundCount));
                pDate = tempDate;
                valueSaleAmount = BigDecimal.ZERO;
                valueSaleCount = BigDecimal.ZERO;
                valueRefundAmount = BigDecimal.ZERO;
                valueRefundCount = BigDecimal.ZERO;
            }
            valueSaleCount = valueSaleCount.add(BigDecimal.ONE);
            Integer status = orderInfo.getOrderStatus();
            if (OrderStatusEnum.REFUNDED.getStatus().equals(status)
                    || OrderStatusEnum.PARTIALLY_REFUNDED.getStatus().equals(status)) {
                valueRefundCount = valueRefundCount.add(BigDecimal.ONE);
            }
            BigDecimal saleAmount = calcEffectiveSaleAmount(orderInfo);
            if (saleAmount.compareTo(BigDecimal.ZERO) > 0) {
                valueSaleAmount = valueSaleAmount.add(saleAmount);
            }
            valueRefundAmount = valueRefundAmount.add(sumNonNormalItemAmount(orderInfo));
        }
        if (valueSaleCount.compareTo(BigDecimal.ZERO) > 0
                || valueSaleAmount.compareTo(BigDecimal.ZERO) > 0
                || valueRefundCount.compareTo(BigDecimal.ZERO) > 0
                || valueRefundAmount.compareTo(BigDecimal.ZERO) > 0) {
            result.add(toDailyVo(pDate, valueSaleAmount, valueSaleCount, valueRefundAmount, valueRefundCount));
        }
        return result;
    }

    private OrderInfoQuery baseQuery(String start, String end) {
        OrderInfoQuery query = new OrderInfoQuery();
        query.setOrderTimeStart(start);
        query.setOrderTimeEnd(end);
        query.setQueryItems(true);
        query.setQueryUser(false);
        return query;
    }

    private OrderDailyStatsVO toDailyVo(String dateStr, BigDecimal saleAmount, BigDecimal saleCount,
                                        BigDecimal refundAmount, BigDecimal refundCount) {
        String day = DateUtil.format(
                DateUtil.parse(dateStr, DateTimePatternEnum.YYYY_MM_DD.getPattern()),
                DateTimePatternEnum.YYYY_MM_DD.getPattern());
        OrderDailyStatsVO vo = new OrderDailyStatsVO();
        vo.setStatisticsDate(day);
        vo.setSaleAmount(saleAmount);
        vo.setSaleCount(saleCount);
        vo.setRefundAmount(refundAmount);
        vo.setRefundCount(refundCount);
        return vo;
    }

    private BigDecimal calcEffectiveSaleAmount(OrderInfo orderInfo) {
        List<OrderItem> items = orderInfo.getOrderItemList();
        if (items == null || items.isEmpty()) {
            return orderInfo.getAmount() == null ? BigDecimal.ZERO : orderInfo.getAmount();
        }
        BigDecimal originalNormal = BigDecimal.ZERO;
        BigDecimal originalAll = BigDecimal.ZERO;
        for (OrderItem orderItem : items) {
            BigDecimal itemAmount = orderItem.getItemAmount() == null ? BigDecimal.ZERO : orderItem.getItemAmount();
            originalAll = originalAll.add(itemAmount);
            if (OrderItemStatusEnum.NORMAL.getStatus().equals(orderItem.getOrderItemStatus())) {
                originalNormal = originalNormal.add(itemAmount);
            }
        }
        if (originalNormal.compareTo(BigDecimal.ZERO) <= 0) {
            return BigDecimal.ZERO;
        }
        BigDecimal orderAmount = orderInfo.getAmount() == null ? BigDecimal.ZERO : orderInfo.getAmount();
        if (originalAll.compareTo(BigDecimal.ZERO) <= 0 || originalNormal.compareTo(originalAll) == 0) {
            return orderAmount;
        }
        return orderAmount.multiply(originalNormal).divide(originalAll, 2, RoundingMode.HALF_UP);
    }

    private BigDecimal sumNonNormalItemAmount(OrderInfo orderInfo) {
        List<OrderItem> items = orderInfo.getOrderItemList();
        if (items == null || items.isEmpty()) {
            return BigDecimal.ZERO;
        }
        BigDecimal refundAmount = BigDecimal.ZERO;
        Set<String> seen = new HashSet<>();
        for (OrderItem orderItem : items) {
            if (orderItem == null) {
                continue;
            }
            String itemId = orderItem.getOrderItemId();
            if (!StringTools.isEmpty(itemId) && !seen.add(itemId)) {
                continue;
            }
            if (!OrderItemStatusEnum.NORMAL.getStatus().equals(orderItem.getOrderItemStatus())) {
                refundAmount = refundAmount.add(
                        orderItem.getItemAmount() == null ? BigDecimal.ZERO : orderItem.getItemAmount());
            }
        }
        return refundAmount;
    }

    private List<OrderInfo> safeList(List<OrderInfo> list) {
        return list == null ? Collections.emptyList() : list;
    }
}
