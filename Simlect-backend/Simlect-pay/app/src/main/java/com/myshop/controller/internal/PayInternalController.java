package com.myshop.controller.internal;

import com.myshop.api.dto.PayCloseDTO;
import com.myshop.api.dto.PayQueryDTO;
import com.myshop.api.dto.PayRefundDTO;
import com.myshop.api.dto.PayTradeCreateDTO;
import com.myshop.api.dto.PayTradeStatusDTO;
import com.myshop.api.dto.PayUrlRequestDTO;
import com.myshop.biz.PayInternalService;
import com.myshop.controller.ABaseController;
import com.myshop.entity.dto.PayInfoDTO;
import com.myshop.entity.dto.PayOrderNotifyDTO;
import com.myshop.entity.vo.ResponseVO;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/internal/pay")
public class PayInternalController extends ABaseController {

    @Resource
    private PayInternalService payInternalService;

    @PostMapping("/trade/createPending")
    public ResponseVO<Void> createPending(@RequestBody PayTradeCreateDTO dto) {
        payInternalService.createPending(dto);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/trade/markSuccess")
    public ResponseVO<Void> markSuccess(@RequestBody PayTradeStatusDTO dto) {
        payInternalService.markSuccess(dto);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/trade/markClosed")
    public ResponseVO<Void> markClosed(@RequestBody PayTradeStatusDTO dto) {
        payInternalService.markClosed(dto);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/trade/markRefunded")
    public ResponseVO<Void> markRefunded(@RequestBody PayTradeStatusDTO dto) {
        payInternalService.markRefunded(dto);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/channel/getPayUrl")
    public ResponseVO<PayInfoDTO> getPayUrl(@RequestBody PayUrlRequestDTO dto) {
        return getSuccessResponseVO(payInternalService.getPayUrl(dto));
    }

    @PostMapping("/channel/refund")
    public ResponseVO<Void> refund(@RequestBody PayRefundDTO dto) {
        payInternalService.refund(dto);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/channel/closeOrder")
    public ResponseVO<Void> closeOrder(@RequestBody PayCloseDTO dto) {
        payInternalService.closeOrder(dto);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/channel/queryOrder")
    public ResponseVO<PayOrderNotifyDTO> queryOrder(@RequestBody PayQueryDTO dto) {
        return getSuccessResponseVO(payInternalService.queryOrder(dto));
    }
}
