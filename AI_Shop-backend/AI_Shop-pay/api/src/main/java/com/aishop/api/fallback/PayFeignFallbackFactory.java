package com.aishop.api.fallback;

import com.aishop.api.PayFeignClient;
import com.aishop.api.dto.PayCloseDTO;
import com.aishop.api.dto.PayQueryDTO;
import com.aishop.api.dto.PayRefundDTO;
import com.aishop.api.dto.PayTradeCreateDTO;
import com.aishop.api.dto.PayTradeStatusDTO;
import com.aishop.api.dto.PayUrlRequestDTO;
import com.aishop.api.support.FeignFallbackResponses;
import com.aishop.api.dto.PayInfoDTO;
import com.aishop.api.dto.PayOrderNotifyDTO;
import com.aishop.entity.vo.ResponseVO;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cloud.openfeign.FallbackFactory;
import org.springframework.stereotype.Component;

@Component
public class PayFeignFallbackFactory implements FallbackFactory<PayFeignClient> {
    private static final Logger log = LoggerFactory.getLogger(PayFeignFallbackFactory.class);

    @Override
    public PayFeignClient create(Throwable cause) {
        return new PayFeignClient() {
            @Override
            public ResponseVO<Void> createPending(PayTradeCreateDTO dto) {
                return FeignFallbackResponses.unavailable(log, "支付服务", cause);
            }

            @Override
            public ResponseVO<Void> markSuccess(PayTradeStatusDTO dto) {
                return FeignFallbackResponses.unavailable(log, "支付服务", cause);
            }

            @Override
            public ResponseVO<Void> markClosed(PayTradeStatusDTO dto) {
                return FeignFallbackResponses.unavailable(log, "支付服务", cause);
            }

            @Override
            public ResponseVO<Void> markRefunded(PayTradeStatusDTO dto) {
                return FeignFallbackResponses.unavailable(log, "支付服务", cause);
            }

            @Override
            public ResponseVO<PayInfoDTO> getPayUrl(PayUrlRequestDTO dto) {
                return FeignFallbackResponses.unavailable(log, "支付服务", cause);
            }

            @Override
            public ResponseVO<Void> refund(PayRefundDTO dto) {
                return FeignFallbackResponses.unavailable(log, "支付服务", cause);
            }

            @Override
            public ResponseVO<Void> closeOrder(PayCloseDTO dto) {
                return FeignFallbackResponses.unavailable(log, "支付服务", cause);
            }

            @Override
            public ResponseVO<PayOrderNotifyDTO> queryOrder(PayQueryDTO dto) {
                return FeignFallbackResponses.unavailable(log, "支付服务", cause);
            }
        };
    }
}
