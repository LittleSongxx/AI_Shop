package com.simlect.biz.impl;

import com.simlect.entity.po.PayTradeRecord;
import com.simlect.entity.query.PayTradeRecordQuery;
import com.simlect.mappers.PayTradeRecordMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.dao.DuplicateKeyException;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doThrow;

@ExtendWith(MockitoExtension.class)
class PayTradeRecordServiceImplTest {

    @Mock
    private PayTradeRecordMapper<PayTradeRecord, PayTradeRecordQuery> mapper;
    @InjectMocks
    private PayTradeRecordServiceImpl service;

    @Test
    void duplicatePayOrderIdIsTreatedAsIdempotentCreate() {
        doThrow(new DuplicateKeyException("duplicate pay_order_id"))
                .when(mapper).insert(any(PayTradeRecord.class));

        assertDoesNotThrow(() -> service.createPending(
                "u1", "pay-1", "order-1", new BigDecimal("99.00"), "ALI_PAY"));
    }
}
