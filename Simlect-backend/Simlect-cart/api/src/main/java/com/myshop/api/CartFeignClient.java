package com.myshop.api;

import com.myshop.api.dto.CartDeleteBatchDTO;
import com.myshop.api.fallback.CartFeignFallbackFactory;
import com.myshop.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

@FeignClient(name = "simlect-cart", contextId = "cartFeignClient", path = "/internal/cart",
        fallbackFactory = CartFeignFallbackFactory.class)
public interface CartFeignClient {

    @PostMapping("/deleteBatch")
    ResponseVO<Void> deleteBatch(@RequestBody CartDeleteBatchDTO dto);
}
