package com.myshop.api.fallback;

import com.myshop.api.SearchToolFeignClient;
import com.myshop.api.support.FeignFallbackResponses;
import com.myshop.entity.vo.ResponseVO;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cloud.openfeign.FallbackFactory;
import org.springframework.stereotype.Component;

@Component
public class SearchToolFeignFallbackFactory implements FallbackFactory<SearchToolFeignClient> {

    private static final Logger log = LoggerFactory.getLogger(SearchToolFeignFallbackFactory.class);

    @Override
    public SearchToolFeignClient create(Throwable cause) {
        return new SearchToolFeignClient() {
            @Override
            public ResponseVO<Void> productData() {
                log.error("SearchToolFeign productData fallback", cause);
                return FeignFallbackResponses.unavailable(log, "搜索服务", cause);
            }

            @Override
            public ResponseVO<Void> ragData() {
                log.error("SearchToolFeign ragData fallback", cause);
                return FeignFallbackResponses.unavailable(log, "搜索服务", cause);
            }
        };
    }
}
