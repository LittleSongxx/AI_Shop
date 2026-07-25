package com.aishop.biz.impl;

import com.aishop.entity.enums.PageSize;
import com.aishop.entity.po.PayTradeRecord;
import com.aishop.entity.query.PayTradeRecordQuery;
import com.aishop.entity.query.SimplePage;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.mappers.PayTradeRecordMapper;
import com.aishop.biz.PayTradeRecordService;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.Date;
import java.util.List;
import java.util.Objects;

@Service("payTradeRecordService")
public class PayTradeRecordServiceImpl implements PayTradeRecordService {

    private static final int STATUS_PENDING = 0;
    private static final int STATUS_SUCCESS = 1;
    private static final int STATUS_CLOSED = 2;
    private static final int STATUS_REFUNDED = 3;

    @Resource
    private PayTradeRecordMapper<PayTradeRecord, PayTradeRecordQuery> payTradeRecordMapper;

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void createPending(String userId, String payOrderId, String orderId, BigDecimal payAmount, String payChannel) {
        if (StringTools.isEmpty(payOrderId) || payAmount == null) {
            return;
        }
        PayTradeRecord record = new PayTradeRecord();
        record.setTradeId(StringTools.createTradeId());
        record.setOrderId(orderId);
        record.setUserId(userId);
        record.setPayOrderId(payOrderId);
        record.setPayChannel(payChannel);
        record.setPayAmount(payAmount);
        record.setTradeStatus(STATUS_PENDING);
        record.setCreateTime(new Date());
        try {
            payTradeRecordMapper.insert(record);
        } catch (DuplicateKeyException ignored) {
            // Database uniqueness is the idempotency boundary for concurrent create requests.
        }
    }

    @Override
    public PayTradeRecord findByPayOrderId(String payOrderId) {
        if (StringTools.isEmpty(payOrderId)) {
            return null;
        }
        return payTradeRecordMapper.selectByPayOrderId(payOrderId);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void markSuccess(String payOrderId, String channelOrderId) {
        if (StringTools.isEmpty(payOrderId)) {
            return;
        }
        int affected = payTradeRecordMapper.updateSuccessIfPending(
                payOrderId, channelOrderId, new Date());
        if (affected > 0) {
            return;
        }
        PayTradeRecord current = findByPayOrderId(payOrderId);
        if (current == null) {
            return;
        }
        if (Objects.equals(current.getTradeStatus(), STATUS_SUCCESS)) {
            return;
        }
        // Closed/refunded records are terminal. A late callback must not reopen them.
        if (Objects.equals(current.getTradeStatus(), STATUS_CLOSED)
                || Objects.equals(current.getTradeStatus(), STATUS_REFUNDED)) {
            return;
        }
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void markClosed(String payOrderId) {
        PayTradeRecordQuery query = new PayTradeRecordQuery();
        query.setPayOrderId(payOrderId);
        query.setTradeStatus(STATUS_PENDING);
        PayTradeRecord update = new PayTradeRecord();
        update.setTradeStatus(STATUS_CLOSED);
        payTradeRecordMapper.updateByParam(update, query);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void markRefunded(String payOrderId) {
        PayTradeRecordQuery query = new PayTradeRecordQuery();
        query.setPayOrderId(payOrderId);
        PayTradeRecord update = new PayTradeRecord();
        update.setTradeStatus(STATUS_REFUNDED);
        payTradeRecordMapper.updateByParam(update, query);
    }

    @Override
    public PaginationResultVO<PayTradeRecord> loadUserTrades(String userId, Integer pageNo) {
        PayTradeRecordQuery query = new PayTradeRecordQuery();
        query.setUserId(userId);
        query.setPageNo(pageNo);
        query.setOrderBy(com.aishop.entity.query.SafeSort.of("create_time desc"));
        int count = payTradeRecordMapper.selectCount(query);
        int pageSize = PageSize.SIZE15.getSize();
        SimplePage page = new SimplePage(pageNo, count, pageSize);
        query.setSimplePage(page);
        List<PayTradeRecord> list = payTradeRecordMapper.selectList(query);
        return new PaginationResultVO<>(count, page.getPageSize(), page.getPageNo(), page.getPageTotal(), list);
    }
}
