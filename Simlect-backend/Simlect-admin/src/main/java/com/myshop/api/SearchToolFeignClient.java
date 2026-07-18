package com.myshop.api;

import com.myshop.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;

@FeignClient(name = "simlect-search", contextId = "searchToolFeignClient", path = "/internal/search/tool",
        fallbackFactory = com.myshop.api.fallback.SearchToolFeignFallbackFactory.class)
public interface SearchToolFeignClient {

    @PostMapping("/productData")
    ResponseVO<Void> productData();

    @PostMapping("/ragData")
    ResponseVO<Void> ragData();
}
