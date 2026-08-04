package com.aishop.controller;

import com.aishop.annotation.GlobalInterceptor;
import com.aishop.component.RedisComponent;
import com.aishop.constants.Constants;
import com.aishop.api.dto.PayInfoDTO;
import com.aishop.api.dto.PostOrderDTO;
import com.aishop.entity.dto.TokenUserInfoDTO;
import com.aishop.api.enums.OrderCommentStatusEnum;
import com.aishop.api.enums.OrderFromTypeEnum;
import com.aishop.api.enums.OrderStatusEnum;
import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.po.OrderItem;
import com.aishop.entity.po.OrderLogisticsInfo;
import com.aishop.entity.query.OrderInfoQuery;
import com.aishop.entity.query.OrderItemQuery;
import com.aishop.api.vo.OrderCountVO;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.exception.BusinessException;
import com.aishop.biz.OrderInfoService;
import com.aishop.biz.OrderItemService;
import com.aishop.biz.OrderLogisticsInfoService;
import com.aishop.biz.OrderRequestIdempotencyService;
import com.aishop.utils.OrderPayAmountUtil;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletResponse;

import java.math.BigDecimal;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

@RequestMapping("/order")
@RestController
public class OrderController extends ABaseController{

    @Resource
    private OrderInfoService orderInfoService;

    @Resource
    private OrderItemService orderItemService;

    @Resource
    private OrderLogisticsInfoService orderLogisticsInfoService;

    @Resource
    private OrderRequestIdempotencyService orderRequestIdempotencyService;

    // 提交订单
    @PostMapping("/postOrder")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO postOrder(
            @Valid @RequestBody PostOrderDTO postOrderDTO,
            @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
            HttpServletResponse response){
        // 根据userId和postOrderDTO生成订单
        PayInfoDTO payInfoDTO = orderInfoService.postOrder(
                getTokenUserInfo().getUserId(), postOrderDTO, idempotencyKey);
        if (Boolean.TRUE.equals(payInfoDTO.getIdempotencyReplayed())) {
            response.setHeader("Idempotency-Replayed", "true");
        }
        return getSuccessResponseVO(payInfoDTO);
    }

    // 获取支付信息
    @PostMapping("/getPayInfo")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO getPayInfo(@NotEmpty String orderId){
        PayInfoDTO payInfoDTO = orderInfoService.getPayInfo(getTokenUserInfo().getUserId(),orderId);
        return getSuccessResponseVO(payInfoDTO);
    }

    // 查询订单信息
    @PostMapping("/getOrderInfo")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO getOrderInfo(@NotEmpty String payOrderId){
        TokenUserInfoDTO tokenUserInfo = getTokenUserInfo();
        if (tokenUserInfo == null || StringTools.isEmpty(tokenUserInfo.getUserId())) {
            throw new BusinessException("登录超时");
        }
        String userId = tokenUserInfo.getUserId();
        // 查询query
        OrderInfoQuery query = new OrderInfoQuery();
        query.setPayOrderId(payOrderId);
        // 根据payOrderId查询订单信息
        List<OrderInfo> orderInfoList = orderInfoService.findListByParam(query);
        if (orderInfoList.isEmpty()) {
            throw new BusinessException("订单不存在");
        }
        OrderInfo orderInfo = orderInfoList.get(0);
        if (!orderInfo.getUserId().equals(userId)){
            throw new BusinessException("订单不存在");
        }
        BigDecimal payTotal = orderInfoList.stream()
                .map(o -> o.getAmount() == null ? BigDecimal.ZERO : o.getAmount())
                .reduce(BigDecimal.ZERO, BigDecimal::add);
        orderInfo.setPayTotalAmount(OrderPayAmountUtil.normalizeChannelPayAmount(payTotal));
        return getSuccessResponseVO(orderInfo);
    }

    // 查询我的订单
    @PostMapping("/loadMyOrder")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO loadMyOrder(@NotNull Integer pageNo, Integer status){
        // 返回PaginationResultVO<OrderInfo> 分页对象，包含订单列表和分页信息
        // 不查询OrderStatus为-1，即已删除的订单
        OrderInfoQuery query = new OrderInfoQuery();
        query.setPageNo(pageNo);
        query.setUserId(getTokenUserInfo().getUserId());
        query.setQueryItems(true);
        query.setOrderBy(com.aishop.entity.query.SafeSort.of("order_time desc"));
        if (status == null){
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
        }else {
            query.setOrderStatus(status);
        }
        // 待评价页：已完成且（未评价）
        if (OrderStatusEnum.WAIT_COMMENT.getStatus().equals(status)) {
            query.setOrderStatus(OrderStatusEnum.COMPLETED.getStatus());
            query.setCommentStatusList(new Integer[]{
                    OrderCommentStatusEnum.NOT_EVALUATED.getStatus(),
            });
        }
        PaginationResultVO<OrderInfo> resultVO = orderInfoService.findListByPage(query);
        return getSuccessResponseVO(resultVO);
    }

    // 取消订单
    @PostMapping("/cancelOrder")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO cancelOrder(@NotEmpty String orderId){
        // 只能取消待支付的本人的订单
//        获得当前用户的userId
        String userId = getTokenUserInfo().getUserId();
        // 根据orderId查询订单
        OrderInfo orderInfo = orderInfoService.getOrderInfoByOrderId(orderId);
        if (orderInfo == null || !orderInfo.getUserId().equals(userId)){
            throw new BusinessException("订单不存在");
        }
        orderInfoService.cancelOrder(userId,orderId,OrderStatusEnum.WAIT_PAYMENT);
        return getSuccessResponseVO(null);
    }

    // 删除订单（更新数据库的orderStatus，逻辑删除）
    @PostMapping("/deleteOrder")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO deleteOrder(@NotEmpty String orderId){
        // 获得当前用户的userId
        String userId = getTokenUserInfo().getUserId();
        // 根据orderId查询订单
        OrderInfo orderInfo = orderInfoService.getOrderInfoByOrderId(orderId);
        if (orderInfo == null || !orderInfo.getUserId().equals(userId)){
            throw new BusinessException("订单不存在");
        }
        // 只能删除3.已完成，4.已取消，5.已关闭，6.已退款的订单
		if (!OrderStatusEnum.COMPLETED.getStatus().equals(orderInfo.getOrderStatus()) &&
				!OrderStatusEnum.CANCELLED.getStatus().equals(orderInfo.getOrderStatus()) &&
				!OrderStatusEnum.CLOSED.getStatus().equals(orderInfo.getOrderStatus()) &&
				!OrderStatusEnum.REFUNDED.getStatus().equals(orderInfo.getOrderStatus()) ){
            throw new BusinessException("当前订单状态无法删除！");
        }
        // 删除订单
        orderInfo.setOrderStatus(OrderStatusEnum.DELETE.getStatus());
        orderInfoService.updateOrderInfoByOrderId(orderInfo,orderId);
        return getSuccessResponseVO(null);
    }

    // 确认订单
    @PostMapping("/confirmOrder")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO confirmOrder(
            @NotEmpty String orderId,
            @RequestHeader(value = "Idempotency-Key", required = false)
            String idempotencyKey){
        String userId = getTokenUserInfo().getUserId();
        executeIdempotentAction(
                userId,
                OrderRequestIdempotencyService.COMMAND_AGENT_CONFIRM_RECEIPT,
                idempotencyKey,
                Map.of("orderId", orderId),
                () -> confirmOrderCommand(userId, orderId));
        return getSuccessResponseVO(null);
    }

    private void confirmOrderCommand(String userId, String orderId) {
        // 根据orderId查询订单
        OrderInfo orderInfo = orderInfoService.getOrderInfoByOrderId(orderId);
        if (orderInfo == null || !orderInfo.getUserId().equals(userId)){
            throw new BusinessException("订单不存在");
        }
        // 只能确认已发货或部分退款的订单
		if (!OrderStatusEnum.SHIPPED.getStatus().equals(orderInfo.getOrderStatus()) &&
			!OrderStatusEnum.PARTIALLY_REFUNDED.getStatus().equals(orderInfo.getOrderStatus()) ){
            throw new BusinessException("当前订单状态无法确认！");
        }
        if (!orderInfoService.confirmOrderReceipt(userId, orderId)) {
            return;
        }
        orderInfoService.onOrderConfirmed(userId, orderId);
    }

    // 退款
    @PostMapping("/refundOrder")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO refundOrder(
            @NotEmpty String orderItemId,
            @RequestHeader(value = "Idempotency-Key", required = false)
            String idempotencyKey){
        String userId = getTokenUserInfo().getUserId();
        executeIdempotentAction(
                userId,
                OrderRequestIdempotencyService.COMMAND_AGENT_REFUND,
                idempotencyKey,
                Map.of("orderItemId", orderItemId),
                () -> refundOrderCommand(userId, orderItemId));
        return getSuccessResponseVO(null);
    }

    private void refundOrderCommand(String userId, String orderItemId) {
        // 根据orderItemId查询订单
        OrderItem orderItem = orderItemService.getOrderItemByOrderItemId(orderItemId);
        if (orderItem == null) {
            throw new BusinessException("订单明细不存在");
        }
        orderInfoService.refund(orderItem, userId);
    }

    // 订单详情（含明细）
    @PostMapping("/getMyOrderDetail")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO getMyOrderDetail(@NotEmpty String orderId){
        String userId = getTokenUserInfo().getUserId();
        OrderInfo orderInfo = orderInfoService.getOrderInfoByOrderId(orderId);
        if (orderInfo == null || !orderInfo.getUserId().equals(userId)){
            throw new BusinessException("订单不存在");
        }
        OrderItemQuery orderItemQuery = new OrderItemQuery();
        orderItemQuery.setOrderId(orderId);
        orderInfo.setOrderItemList(orderItemService.findListByParam(orderItemQuery));
        orderInfoService.enrichCouponInfo(orderInfo);
        return getSuccessResponseVO(orderInfo);
    }

    // 查看物流
    @PostMapping("/getLogistics")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO getLogistics(@NotEmpty String orderId){
        return getSuccessResponseVO(orderLogisticsInfoService.getOrderLogisticsRecords(getTokenUserInfo().getUserId(), orderId));
    }

    // 获取订单数量 待付款、待发货、待收货、待评价
    @GetMapping("/getOrderCountInfo")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO getOrderCountInfo(){
        String userId = getTokenUserInfo().getUserId();
        List<OrderCountVO> orderCountVOList = orderInfoService.getOrderCountInfo(userId);
        return getSuccessResponseVO(orderCountVOList);
    }

    private void executeIdempotentAction(
            String userId,
            String commandType,
            String idempotencyKey,
            Object request,
            Runnable command) {
        if (StringTools.isEmpty(idempotencyKey)) {
            command.run();
            return;
        }
        orderRequestIdempotencyService.executeMap(
                userId,
                commandType,
                idempotencyKey,
                request,
                () -> {
                    command.run();
                    return Map.of("success", true);
                });
    }

}
