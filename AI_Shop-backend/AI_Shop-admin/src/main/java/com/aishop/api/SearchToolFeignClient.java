package com.aishop.api;

import com.aishop.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;

@FeignClient(name = "aishop-search", contextId = "searchToolFeignClient", path = "/internal/search/tool",
        fallbackFactory = com.aishop.api.fallback.SearchToolFeignFallbackFactory.class)
public interface SearchToolFeignClient {

    @PostMapping("/productData")
    ResponseVO<Void> productData();

    @PostMapping("/ragData")
    ResponseVO<Void> ragData();
}
