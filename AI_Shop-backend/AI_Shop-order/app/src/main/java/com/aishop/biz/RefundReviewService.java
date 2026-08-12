package com.aishop.biz;

import com.aishop.api.enums.OrderItemStatusEnum;
import com.aishop.entity.enums.RefundSagaStatus;
import com.aishop.entity.po.OrderItem;
import com.aishop.entity.po.RefundRequest;
import com.aishop.entity.po.RefundReviewLedger;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.OrderItemMapper;
import com.aishop.mappers.RefundRequestMapper;
import com.aishop.mappers.RefundReviewLedgerMapper;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.List;
import java.util.Objects;

/**
 * 退款人工复核管理端：审批 MANUAL_REVIEW 死路，恢复 Saga 继续推进。
 *
 * 两条保证（与 Agent 写命令的幂等台账同一套思路）：
 * 1. 幂等——review_id 由客户端生成，重试复用同一键；INSERT IGNORE 唯一约束
 *    使重复提交在台账层被吸收，审批动作本身不会执行两遍。
 * 2. 并发单次生效——状态翻转用 CAS（status = 'MANUAL_REVIEW' 条件更新），
 *    两个管理端同时审批同一笔时只有一个 affected = 1，另一方拿到冲突异常。
 */
@Service
public class RefundReviewService {

    private static final int MAX_PENDING_LIMIT = 100;

    @Resource
    private RefundRequestMapper refundRequestMapper;
    @Resource
    private RefundReviewLedgerMapper ledgerMapper;
    @Resource
    private OrderItemMapper<OrderItem, ?> orderItemMapper;

    public List<RefundRequest> listPendingReviews(Integer limit) {
        int bounded = limit == null ? MAX_PENDING_LIMIT : Math.max(1, Math.min(limit, MAX_PENDING_LIMIT));
        return refundRequestMapper.selectManualReview(bounded);
    }

    @Transactional(rollbackFor = Exception.class)
    public RefundRequest approve(String refundRequestId, String reviewId,
                                 String operator, String reason) {
        RefundReviewLedger existing = ledgerMapper.selectByReviewId(reviewId);
        if (existing != null) {
            // 同一 review_id 重试：幂等命中，返回当前状态而非重复审批。
            if (!refundRequestId.equals(existing.getRefundRequestId())) {
                throw new BusinessException("审批编号冲突");
            }
            if (!"APPROVE".equals(existing.getAction())) {
                // 幂等键被跨动作复用会让调用方把"无操作"误判为成功，必须拒绝。
                throw new BusinessException("审批编号已被其他审批动作使用");
            }
            RefundRequest current = refundRequestMapper.selectById(refundRequestId);
            if (current != null) {
                return current;
            }
        }
        RefundRequest request = refundRequestMapper.selectByIdForUpdate(refundRequestId);
        if (request == null) {
            throw new BusinessException("退款请求不存在");
        }
        if (!RefundSagaStatus.MANUAL_REVIEW.name().equals(request.getStatus())) {
            throw new BusinessException("退款请求不在人工复核状态");
        }
        // 人工复核窗口期内业务侧可能改价/改属性/退订，恢复推进前按冻结字段重新校验。
        revalidateFrozenFields(request);
        String origin = resolveOrigin(request.getReviewOriginStatus());
        if (refundRequestMapper.reviewApprove(refundRequestId, origin) != 1) {
            // CAS 失败：同 review_id 并发时台账已落（幂等返回）；否则是另一审批
            // 已生效，让调用方刷新后重试。
            RefundReviewLedger already = ledgerMapper.selectByReviewId(reviewId);
            if (already != null && refundRequestId.equals(already.getRefundRequestId())) {
                return refundRequestMapper.selectById(refundRequestId);
            }
            throw new BusinessException("审批冲突，请刷新后重试");
        }
        insertLedger(refundRequestId, reviewId, "APPROVE", operator, reason);
        return refundRequestMapper.selectById(refundRequestId);
    }

    @Transactional(rollbackFor = Exception.class)
    public RefundRequest reject(String refundRequestId, String reviewId,
                                String operator, String reason) {
        RefundReviewLedger existing = ledgerMapper.selectByReviewId(reviewId);
        if (existing != null) {
            if (!refundRequestId.equals(existing.getRefundRequestId())) {
                throw new BusinessException("审批编号冲突");
            }
            if (!"REJECT".equals(existing.getAction())) {
                throw new BusinessException("审批编号已被其他审批动作使用");
            }
            RefundRequest current = refundRequestMapper.selectById(refundRequestId);
            if (current != null) {
                return current;
            }
        }
        RefundRequest request = refundRequestMapper.selectByIdForUpdate(refundRequestId);
        if (request == null) {
            throw new BusinessException("退款请求不存在");
        }
        if (!RefundSagaStatus.MANUAL_REVIEW.name().equals(request.getStatus())) {
            throw new BusinessException("退款请求不在人工复核状态");
        }
        if (refundRequestMapper.reviewReject(refundRequestId) != 1) {
            RefundReviewLedger already = ledgerMapper.selectByReviewId(reviewId);
            if (already != null && refundRequestId.equals(already.getRefundRequestId())) {
                return refundRequestMapper.selectById(refundRequestId);
            }
            throw new BusinessException("审批冲突，请刷新后重试");
        }
        insertLedger(refundRequestId, reviewId, "REJECT", operator, reason);
        return refundRequestMapper.selectById(refundRequestId);
    }

    private void insertLedger(String refundRequestId, String reviewId,
                              String action, String operator, String reason) {
        RefundReviewLedger ledger = new RefundReviewLedger();
        ledger.setRefundRequestId(refundRequestId);
        ledger.setReviewId(reviewId);
        ledger.setAction(action);
        ledger.setOperator(operator);
        ledger.setReason(reason);
        // 幂等检查在行锁内已完成，INSERT IGNORE 兜底并发窗口（同 review_id 竞态）。
        ledgerMapper.insertIgnore(ledger);
    }

    /**
     * 冻结字段校验：退款申请落库时冻结的金额/数量/属性，与订单项当前值必须一致，
     * 订单项仍须处于 NORMAL——不一致说明人工窗口期发生了数据漂移，
     * 此时按冻结值推进会退错钱，审批应停在 MANUAL_REVIEW 交由管理端决定（驳回或核对）。
     *
     * 例外：STOCK_PENDING 阶段进入复核的请求——资金已退回、订单项已被
     * finalizeOrderRefund 翻为 REFUND(0)，剩余工作只是库存恢复，冻结值
     * 校验失去对象（金额/数量/属性在此阶段不再变化），只保留明细存在性防御。
     * 阶段由 review_origin_status 携带，null（旧数据）按 PENDING_PAYMENT 保守处理。
     */
    private void revalidateFrozenFields(RefundRequest request) {
        OrderItem item = orderItemMapper.selectByOrderItemId(request.getOrderItemId());
        if (item == null) {
            throw new BusinessException("订单明细不存在，无法恢复退款");
        }
        if (RefundSagaStatus.STOCK_PENDING.name().equals(request.getReviewOriginStatus())) {
            // 资金已出、明细已翻 REFUND：跳过状态与冻结值校验，只做库存恢复。
            return;
        }
        if (!OrderItemStatusEnum.NORMAL.getStatus().equals(item.getOrderItemStatus())) {
            throw new BusinessException("订单明细状态已变化，请核实后重新审批");
        }
        if (!eqAmount(request.getRefundAmount(), item.getItemAmount())) {
            throw new BusinessException("退款金额与订单项当前金额不一致，请核实后重新审批");
        }
        if (!Objects.equals(request.getBuyCount(), item.getBuyCount())) {
            throw new BusinessException("退款数量与订单项当前数量不一致，请核实后重新审批");
        }
        if (!Objects.equals(request.getPropertyValueIdHash(), item.getPropertyValueIdHash())) {
            throw new BusinessException("商品属性与订单项当前属性不一致，请核实后重新审批");
        }
    }

    private static boolean eqAmount(BigDecimal a, BigDecimal b) {
        if (a == null || b == null) {
            return a == b;
        }
        return a.compareTo(b) == 0;
    }

    /** 恢复目标：只允许回到 Saga 原有阶段；异常数据防御性回落 PENDING_PAYMENT。 */
    private static String resolveOrigin(String originStatus) {
        if (RefundSagaStatus.PENDING_PAYMENT.name().equals(originStatus)
                || RefundSagaStatus.STOCK_PENDING.name().equals(originStatus)) {
            return originStatus;
        }
        return RefundSagaStatus.PENDING_PAYMENT.name();
    }
}
