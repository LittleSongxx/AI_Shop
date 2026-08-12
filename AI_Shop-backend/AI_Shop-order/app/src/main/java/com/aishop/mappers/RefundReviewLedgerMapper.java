package com.aishop.mappers;

import com.aishop.entity.po.RefundReviewLedger;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

public interface RefundReviewLedgerMapper {

    @Insert("""
            INSERT IGNORE INTO refund_review_ledger
                (refund_request_id, review_id, action, operator, reason, created_at)
            VALUES
                (#{refundRequestId}, #{reviewId}, #{action}, #{operator}, #{reason}, NOW())
            """)
    int insertIgnore(RefundReviewLedger ledger);

    @Select("SELECT * FROM refund_review_ledger WHERE review_id = #{reviewId}")
    RefundReviewLedger selectByReviewId(@Param("reviewId") String reviewId);
}
