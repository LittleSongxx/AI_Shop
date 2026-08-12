package com.aishop.controller.admin;

import com.aishop.biz.RefundReviewService;
import com.aishop.entity.po.RefundRequest;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.exception.BusinessException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RefundReviewControllerTest {

    private final RefundReviewService service = mock(RefundReviewService.class);
    private RefundReviewController controller;

    @BeforeEach
    void setUp() {
        controller = new RefundReviewController();
        ReflectionTestUtils.setField(controller, "refundReviewService", service);
    }

    @Test
    void loadDataListReturnsPendingReviews() {
        RefundRequest request = new RefundRequest();
        request.setRefundRequestId("r1");
        when(service.listPendingReviews(null)).thenReturn(List.of(request));

        ResponseVO<List<RefundRequest>> response = controller.loadDataList(null);

        assertEquals("success", response.getStatus());
        assertEquals(1, response.getData().size());
    }

    @Test
    void approveForwardsParamsAndReturnsRestoredRequest() {
        RefundRequest restored = new RefundRequest();
        restored.setRefundRequestId("r1");
        restored.setStatus("PENDING_PAYMENT");
        when(service.approve("r1", "rv-1", "admin", "ok")).thenReturn(restored);

        ResponseVO<RefundRequest> response =
                controller.approve("r1", "rv-1", "admin", "ok");

        assertEquals("success", response.getStatus());
        assertTrue(response.getData().getStatus().startsWith("PENDING"));
        verify(service).approve("r1", "rv-1", "admin", "ok");
    }

    @Test
    void rejectForwardsParams() {
        when(service.reject("r1", "rv-2", "admin", "no")).thenReturn(new RefundRequest());

        ResponseVO<RefundRequest> response = controller.reject("r1", "rv-2", "admin", "no");

        assertEquals("success", response.getStatus());
        verify(service).reject("r1", "rv-2", "admin", "no");
    }

    @Test
    void approveRejectsMissingParams() {
        assertThrows(BusinessException.class,
                () -> controller.approve(null, "rv-1", "admin", null));
        assertThrows(BusinessException.class,
                () -> controller.approve("r1", "", "admin", null));
    }

    @Test
    void serviceErrorsPropagate() {
        when(service.approve("r1", "rv-1", "admin", null))
                .thenThrow(new BusinessException("审批冲突，请刷新后重试"));

        BusinessException exc = assertThrows(BusinessException.class,
                () -> controller.approve("r1", "rv-1", "admin", null));

        assertEquals("审批冲突，请刷新后重试", exc.getMessage());
    }
}
