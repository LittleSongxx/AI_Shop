package com.myshop.api;

import com.myshop.api.dto.PayCloseDTO;
import com.myshop.api.dto.PayQueryDTO;
import com.myshop.api.dto.PayRefundDTO;
import com.myshop.api.dto.PayTradeCreateDTO;
import com.myshop.api.dto.PayTradeStatusDTO;
import com.myshop.api.dto.PayUrlRequestDTO;
import com.myshop.api.fallback.PayFeignFallbackFactory;
import com.myshop.entity.dto.PayInfoDTO;
import com.myshop.entity.dto.PayOrderNotifyDTO;
import com.myshop.entity.vo.ResponseVO;
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
