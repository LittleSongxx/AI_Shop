package com.aishop.controller.admin;

import com.aishop.biz.RefundReviewService;
import com.aishop.entity.po.RefundRequest;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.exception.BusinessException;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 退款人工复核管理端。
 *
 * 服务的是 Saga 死路：重试耗尽后进入 MANUAL_REVIEW 的退款请求，
 * 由这里审批——通过则按进入前的原阶段恢复（定时器自动接管），
 * 驳回则终态 REJECTED。审批幂等键 review_id 由调用方生成，重试复用。
 */
@RestController("adminRefundReviewController")
@RequestMapping("/admin/refundReview")
public class RefundReviewController extends com.aishop.controller.admin.ABaseController {

    @Resource
    private RefundReviewService refundReviewService;

    @PostMapping("/loadDataList")
    public ResponseVO<List<RefundRequest>> loadDataList(Integer limit) {
        return getSuccessResponseVO(refundReviewService.listPendingReviews(limit));
    }

    @PostMapping("/approve")
    public ResponseVO<RefundRequest> approve(String refundRequestId, String reviewId,
                                             String operator, String reason) {
        validateRequired(refundRequestId, reviewId);
        return getSuccessResponseVO(
                refundReviewService.approve(refundRequestId, reviewId, operator, reason));
    }

    @PostMapping("/reject")
    public ResponseVO<RefundRequest> reject(String refundRequestId, String reviewId,
                                            String operator, String reason) {
        validateRequired(refundRequestId, reviewId);
        return getSuccessResponseVO(
                refundReviewService.reject(refundRequestId, reviewId, operator, reason));
    }

    private static void validateRequired(String refundRequestId, String reviewId) {
        if (refundRequestId == null || refundRequestId.isBlank()
                || reviewId == null || reviewId.isBlank()) {
            throw new BusinessException("退款请求编号与审批编号不能为空");
        }
    }
}
