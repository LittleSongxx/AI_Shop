package com.simlect.api.fallback;

import com.simlect.api.PayFeignClient;
import com.simlect.api.dto.PayCloseDTO;
import com.simlect.api.dto.PayQueryDTO;
import com.simlect.api.dto.PayRefundDTO;
import com.simlect.api.dto.PayTradeCreateDTO;
import com.simlect.api.dto.PayTradeStatusDTO;
import com.simlect.api.dto.PayUrlRequestDTO;
import com.simlect.api.support.FeignFallbackResponses;
import com.simlect.api.dto.PayInfoDTO;
import com.simlect.api.dto.PayOrderNotifyDTO;
import com.simlect.entity.vo.ResponseVO;
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
