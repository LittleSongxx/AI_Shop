package com.aishop.api;

import com.aishop.api.dto.PayCloseDTO;
import com.aishop.api.dto.PayQueryDTO;
import com.aishop.api.dto.PayRefundDTO;
import com.aishop.api.dto.PayTradeCreateDTO;
import com.aishop.api.dto.PayTradeStatusDTO;
import com.aishop.api.dto.PayUrlRequestDTO;
import com.aishop.api.fallback.PayFeignFallbackFactory;
import com.aishop.api.dto.PayInfoDTO;
import com.aishop.api.dto.PayOrderNotifyDTO;
import com.aishop.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

@FeignClient(name = "aishop-pay", contextId = "payFeignClient", path = "/internal/pay",
        fallbackFactory = PayFeignFallbackFactory.class)
public interface PayFeignClient {

    @PostMapping("/trade/createPending")
    ResponseVO<Void> createPending(@RequestBody PayTradeCreateDTO dto);

    @PostMapping("/trade/markSuccess")
    ResponseVO<Void> markSuccess(@RequestBody PayTradeStatusDTO dto);

    @PostMapping("/trade/markClosed")
    ResponseVO<Void> markClosed(@RequestBody PayTradeStatusDTO dto);

    @PostMapping("/trade/markRefunded")
    ResponseVO<Void> markRefunded(@RequestBody PayTradeStatusDTO dto);

    @PostMapping("/channel/getPayUrl")
    ResponseVO<PayInfoDTO> getPayUrl(@RequestBody PayUrlRequestDTO dto);

    @PostMapping("/channel/refund")
    ResponseVO<Void> refund(@RequestBody PayRefundDTO dto);

    @PostMapping("/channel/closeOrder")
    ResponseVO<Void> closeOrder(@RequestBody PayCloseDTO dto);

    @PostMapping("/channel/queryOrder")
    ResponseVO<PayOrderNotifyDTO> queryOrder(@RequestBody PayQueryDTO dto);
}
