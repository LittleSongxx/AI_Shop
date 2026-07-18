package com.myshop.api.fallback;

import com.myshop.api.CartFeignClient;
import com.myshop.api.dto.CartDeleteBatchDTO;
import com.myshop.api.support.FeignFallbackResponses;
import com.myshop.entity.vo.ResponseVO;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cloud.openfeign.FallbackFactory;
import org.springframework.stereotype.Component;

@Component
public class CartFeignFallbackFactory implements FallbackFactory<CartFeignClient> {
    private static final Logger log = LoggerFactory.getLogger(CartFeignFallbackFactory.class);

    @Override
    public CartFeignClient create(Throwable cause) {
        return dto -> {
            log.error("CartFeign deleteBatch fallback", cause);
            return FeignFallbackResponses.unavailable("购物车服务");
        };
    }
}
