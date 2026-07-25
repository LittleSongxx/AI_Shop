package com.aishop.api;

import com.aishop.api.dto.CartDeleteBatchDTO;
import com.aishop.api.fallback.CartFeignFallbackFactory;
import com.aishop.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

@FeignClient(name = "aishop-cart", contextId = "cartFeignClient", path = "/internal/cart",
        fallbackFactory = CartFeignFallbackFactory.class)
public interface CartFeignClient {

    @PostMapping("/deleteBatch")
    ResponseVO<Void> deleteBatch(@RequestBody CartDeleteBatchDTO dto);
}
