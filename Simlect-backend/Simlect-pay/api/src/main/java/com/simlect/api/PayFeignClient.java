package com.simlect.api;

import com.simlect.api.dto.PayCloseDTO;
import com.simlect.api.dto.PayQueryDTO;
import com.simlect.api.dto.PayRefundDTO;
import com.simlect.api.dto.PayTradeCreateDTO;
import com.simlect.api.dto.PayTradeStatusDTO;
import com.simlect.api.dto.PayUrlRequestDTO;
import com.simlect.api.fallback.PayFeignFallbackFactory;
import com.simlect.api.dto.PayInfoDTO;
import com.simlect.api.dto.PayOrderNotifyDTO;
import com.simlect.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

@FeignClient(name = "simlect-pay", contextId = "payFeignClient", path = "/internal/pay",
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
