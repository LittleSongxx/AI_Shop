package com.aishop.mappers;

import com.aishop.entity.po.RefundRequest;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.util.List;

public interface RefundRequestMapper {

    @Insert("""
            INSERT IGNORE INTO refund_request
                (refund_request_id, refund_order_no, source_pay_order_id, order_id, order_item_id,
                 user_id, product_id, property_value_id_hash, buy_count, refund_amount, pay_channel,
                 status, retry_count, next_retry_time, created_at, updated_at)
            VALUES
                (#{refundRequestId}, #{refundOrderNo}, #{sourcePayOrderId}, #{orderId}, #{orderItemId},
                 #{userId}, #{productId}, #{propertyValueIdHash}, #{buyCount}, #{refundAmount},
                 #{payChannel}, #{status}, 0, NOW(), NOW(), NOW())
            """)
    int insertIgnore(RefundRequest request);

    @Select("SELECT * FROM refund_request WHERE refund_request_id = #{refundRequestId}")
    RefundRequest selectById(@Param("refundRequestId") String refundRequestId);

    @Select("SELECT * FROM refund_request WHERE order_item_id = #{orderItemId}")
    RefundRequest selectByOrderItemId(@Param("orderItemId") String orderItemId);

    @Select("SELECT * FROM refund_request WHERE refund_request_id = #{refundRequestId} FOR UPDATE")
    RefundRequest selectByIdForUpdate(@Param("refundRequestId") String refundRequestId);

    @Update("""
            UPDATE refund_request
            SET retry_count = retry_count + 1,
                next_retry_time = DATE_ADD(NOW(), INTERVAL #{leaseSeconds} SECOND),
                updated_at = NOW()
            WHERE refund_request_id = #{refundRequestId}
              AND status = 'PENDING_PAYMENT'
              AND (next_retry_time IS NULL OR next_retry_time <= NOW())
            """)
    int claimPaymentAttempt(@Param("refundRequestId") String refundRequestId,
                            @Param("leaseSeconds") int leaseSeconds);

    @Update("""
            UPDATE refund_request
            SET status = 'PAYMENT_CONFIRMED',
                payment_confirmed_at = NOW(),
                next_retry_time = NOW(),
                last_error = NULL,
                updated_at = NOW()
            WHERE refund_request_id = #{refundRequestId}
              AND status = 'PENDING_PAYMENT'
            """)
    int markPaymentConfirmed(@Param("refundRequestId") String refundRequestId);

    @Update("""
            UPDATE refund_request
            SET next_retry_time = DATE_ADD(NOW(), INTERVAL #{retrySeconds} SECOND),
                last_error = #{error},
                status = CASE WHEN retry_count >= #{maxRetries}
                              THEN 'MANUAL_REVIEW' ELSE status END,
                review_origin_status = CASE WHEN retry_count >= #{maxRetries}
                              THEN COALESCE(review_origin_status, 'PENDING_PAYMENT')
                              ELSE review_origin_status END,
                updated_at = NOW()
            WHERE refund_request_id = #{refundRequestId}
              AND status = 'PENDING_PAYMENT'
            """)
    int recordPaymentFailure(@Param("refundRequestId") String refundRequestId,
                             @Param("retrySeconds") int retrySeconds,
                             @Param("maxRetries") int maxRetries,
                             @Param("error") String error);

    @Update("""
            UPDATE refund_request
            SET status = 'STOCK_PENDING',
                next_retry_time = DATE_ADD(NOW(), INTERVAL #{retrySeconds} SECOND),
                last_error = NULL,
                updated_at = NOW()
            WHERE refund_request_id = #{refundRequestId}
              AND status = 'PAYMENT_CONFIRMED'
            """)
    int markStockPending(@Param("refundRequestId") String refundRequestId,
                         @Param("retrySeconds") int retrySeconds);

    @Update("""
            UPDATE refund_request
            SET retry_count = retry_count + 1,
                next_retry_time = DATE_ADD(NOW(), INTERVAL #{retrySeconds} SECOND),
                last_error = #{error},
                status = CASE WHEN retry_count + 1 >= #{maxRetries}
                              THEN 'MANUAL_REVIEW' ELSE status END,
                review_origin_status = CASE WHEN retry_count + 1 >= #{maxRetries}
                              THEN COALESCE(review_origin_status, 'STOCK_PENDING')
                              ELSE review_origin_status END,
                updated_at = NOW()
            WHERE refund_request_id = #{refundRequestId}
              AND status = 'STOCK_PENDING'
            """)
    int recordStockRetry(@Param("refundRequestId") String refundRequestId,
                         @Param("retrySeconds") int retrySeconds,
                         @Param("maxRetries") int maxRetries,
                         @Param("error") String error);

    @Update("""
            UPDATE refund_request
            SET status = 'COMPLETED',
                completed_at = NOW(),
                next_retry_time = NULL,
                last_error = NULL,
                updated_at = NOW()
            WHERE refund_request_id = #{refundRequestId}
              AND status IN ('STOCK_PENDING', 'COMPLETED')
            """)
    int markCompleted(@Param("refundRequestId") String refundRequestId);

    @Select("""
            SELECT * FROM refund_request
            WHERE status IN ('PENDING_PAYMENT', 'PAYMENT_CONFIRMED', 'STOCK_PENDING')
              AND (next_retry_time IS NULL OR next_retry_time <= NOW())
            ORDER BY updated_at ASC
            LIMIT #{limit}
            """)
    List<RefundRequest> selectDue(@Param("limit") int limit);

    @Select("""
            SELECT * FROM refund_request
            WHERE status = 'MANUAL_REVIEW'
            ORDER BY updated_at ASC
            LIMIT #{limit}
            """)
    List<RefundRequest> selectManualReview(@Param("limit") int limit);

    @Update("""
            UPDATE refund_request
            SET status = #{originStatus},
                retry_count = 0,
                next_retry_time = NOW(),
                last_error = NULL,
                review_origin_status = NULL,
                updated_at = NOW()
            WHERE refund_request_id = #{refundRequestId}
              AND status = 'MANUAL_REVIEW'
            """)
    int reviewApprove(@Param("refundRequestId") String refundRequestId,
                      @Param("originStatus") String originStatus);

    @Update("""
            UPDATE refund_request
            SET status = 'REJECTED',
                next_retry_time = NULL,
                last_error = NULL,
                updated_at = NOW()
            WHERE refund_request_id = #{refundRequestId}
              AND status = 'MANUAL_REVIEW'
            """)
    int reviewReject(@Param("refundRequestId") String refundRequestId);

    /**
     * 用户重开被驳回的退款：REJECTED -> review_origin_status（驳回前阶段）。
     * COALESCE 兜底：旧数据（origin 为 NULL）按 PENDING_PAYMENT 处理；
     * 但 STOCK_PENDING 驳回的行保留 origin，重开后直接续跑库存恢复，
     * 不会重走支付——资金已退过的行再走支付是重复退款风险。
     * 恢复后清空 origin：下一次重试耗尽重新记录新阶段。
     */
    @Update("""
            UPDATE refund_request
            SET status = COALESCE(review_origin_status, 'PENDING_PAYMENT'),
                retry_count = 0,
                next_retry_time = NOW(),
                last_error = NULL,
                review_origin_status = NULL,
                updated_at = NOW()
            WHERE refund_request_id = #{refundRequestId}
              AND status = 'REJECTED'
            """)
    int resetRejected(@Param("refundRequestId") String refundRequestId);
}
