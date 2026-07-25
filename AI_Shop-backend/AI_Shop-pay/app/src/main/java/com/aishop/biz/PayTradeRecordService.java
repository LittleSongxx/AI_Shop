package com.aishop.biz;

import com.aishop.entity.po.PayTradeRecord;
import com.aishop.entity.vo.PaginationResultVO;

import java.math.BigDecimal;

public interface PayTradeRecordService {

    void createPending(String userId, String payOrderId, String orderId, BigDecimal payAmount, String payChannel);

    PayTradeRecord findByPayOrderId(String payOrderId);

    void markSuccess(String payOrderId, String channelOrderId);

    void markClosed(String payOrderId);

    void markRefunded(String payOrderId);

    PaginationResultVO<PayTradeRecord> loadUserTrades(String userId, Integer pageNo);
}
