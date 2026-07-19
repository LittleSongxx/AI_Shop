package com.simlect.api;

import com.simlect.api.dto.CartDeleteBatchDTO;
import com.simlect.api.fallback.CartFeignFallbackFactory;
import com.simlect.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

@FeignClient(name = "simlect-cart", contextId = "cartFeignClient", path = "/internal/cart",
        fallbackFactory = CartFeignFallbackFactory.class)
public interface CartFeignClient {

    @PostMapping("/deleteBatch")
    ResponseVO<Void> deleteBatch(@RequestBody CartDeleteBatchDTO dto);
}
