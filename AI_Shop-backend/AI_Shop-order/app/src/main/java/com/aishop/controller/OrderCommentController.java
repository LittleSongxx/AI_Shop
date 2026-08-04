package com.aishop.controller;

import com.aishop.annotation.GlobalInterceptor;
import com.aishop.api.enums.CommentStatusEnum;
import com.aishop.entity.po.OrderComment;
import com.aishop.entity.query.OrderCommentQuery;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.OrderCommentService;
import com.aishop.biz.OrderRequestIdempotencyService;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import jakarta.validation.constraints.*;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.Supplier;
@RequestMapping("/order/comment")
@RestController
public class OrderCommentController extends ABaseController{

    @Resource
    private OrderCommentService orderCommentService;

    @Resource
    private OrderRequestIdempotencyService orderRequestIdempotencyService;

    // 评价
    @PostMapping("/postComment")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO postComment(
            @NotEmpty String orderId,
            @NotEmpty @Size(max =300) String commentContent,
            @Size(max =2000) String commentImages,
            @NotNull @Min(1) @Max(5) Integer star,
            @RequestHeader(value = "Idempotency-Key", required = false)
            String idempotencyKey){
        String userId = getTokenUserInfo().getUserId();
        Map<String, Object> request = new LinkedHashMap<>();
        request.put("orderId", orderId);
        request.put("commentContent", commentContent);
        request.put("commentImages", commentImages);
        request.put("star", star);
        Map<String, Object> result = executeIdempotentAction(
                userId,
                OrderRequestIdempotencyService.COMMAND_AGENT_PRODUCT_REVIEW,
                idempotencyKey,
                request,
                () -> Map.of(
                        "pendingReview",
                        orderCommentService.postComment(
                                userId,
                                orderId,
                                commentContent,
                                commentImages,
                                star)));
        Map<String, Object> data = new HashMap<>();
        data.put("pendingReview", Boolean.TRUE.equals(result.get("pendingReview")));
        return getSuccessResponseVO(data);
    }

    // 获取商品所有评论
    @PostMapping("/loadComment")
    public ResponseVO loadComment(@NotNull Integer pageNo, @NotEmpty String productId){
        OrderCommentQuery orderCommentQuery = new OrderCommentQuery();
        orderCommentQuery.setProductId(productId);
        orderCommentQuery.setPageNo(pageNo);
        orderCommentQuery.setQueryUserInfo(true);
        orderCommentQuery.setStatus(CommentStatusEnum.NORMAL.getStatus());
        orderCommentQuery.setOrderBy(com.aishop.entity.query.SafeSort.of("comment_time desc"));
        PaginationResultVO<OrderComment> paginationResultVO = orderCommentService.findListByPage(orderCommentQuery);
        return getSuccessResponseVO(paginationResultVO);
    }

    // 获取单个orderId的评论
    @PostMapping("/getComment")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO getComment(@NotEmpty String orderId){
        String userId = getTokenUserInfo().getUserId();
        OrderComment orderComment = orderCommentService.getComment(userId,orderId);
        return getSuccessResponseVO(orderComment);
    }

    // 追评
    @PostMapping("/postReComment")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO postReComment(
            @NotEmpty String orderId,
            @NotEmpty @Size(max =300) String reCommentContent,
            @Size(max =2000) String reCommentImages,
            @RequestHeader(value = "Idempotency-Key", required = false)
            String idempotencyKey){
        // 获取当前用户的userId
        String userId = getTokenUserInfo().getUserId();
        Map<String, Object> request = new LinkedHashMap<>();
        request.put("orderId", orderId);
        request.put("reCommentContent", reCommentContent);
        request.put("reCommentImages", reCommentImages);
        executeIdempotentAction(
                userId,
                OrderRequestIdempotencyService.COMMAND_AGENT_RECOMMENT,
                idempotencyKey,
                request,
                () -> {
                    orderCommentService.postReComment(
                            userId, orderId, reCommentContent, reCommentImages);
                    return Map.of("success", true);
                });
        return getSuccessResponseVO(null);
    }

    // 获取我的评论
    @PostMapping("/loadMyComment")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO loadMyComment(@NotNull Integer pageNo){
        String userId = getTokenUserInfo().getUserId();
        OrderCommentQuery orderCommentQuery = new OrderCommentQuery();
        orderCommentQuery.setUserId(userId);
        orderCommentQuery.setPageNo(pageNo);
        orderCommentQuery.setQueryUserInfo(true);
        orderCommentQuery.setQueryProduct(true);
        orderCommentQuery.setOrderBy(com.aishop.entity.query.SafeSort.of("comment_time desc"));
        orderCommentQuery.setStatus(CommentStatusEnum.NORMAL.getStatus());
        PaginationResultVO<OrderComment> paginationResultVO = orderCommentService.findListByPage(orderCommentQuery);
        return getSuccessResponseVO(paginationResultVO);
    }

    // 删除我的评论
    @PostMapping("/delMyComment")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO delMyComment(@NotEmpty String orderId){
        String userId = getTokenUserInfo().getUserId();
        orderCommentService.delMyComment(userId, orderId);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/getProductCommentStats")
    public ResponseVO getProductCommentStats(String productId) {
        return getSuccessResponseVO(orderCommentService.getProductCommentStats(productId));
    }

    private Map<String, Object> executeIdempotentAction(
            String userId,
            String commandType,
            String idempotencyKey,
            Object request,
            Supplier<Map<String, Object>> command) {
        if (StringTools.isEmpty(idempotencyKey)) {
            return command.get();
        }
        return orderRequestIdempotencyService.executeMap(
                userId,
                commandType,
                idempotencyKey,
                request,
                command);
    }
}
