package com.simlect.api.support;

import com.simlect.api.SearchToolFeignClient;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Component;

@Component
public class SearchToolFeignSupport {

    @Resource
    private SearchToolFeignClient searchToolFeignClient;
    @Resource
    private FeignResponseSupport feignResponseSupport;

    public void productData() {
        feignResponseSupport.run(searchToolFeignClient::productData, "同步商品搜索/RAG数据失败");
    }

    public void ragData() {
        feignResponseSupport.run(searchToolFeignClient::ragData, "同步RAG FAQ数据失败");
    }
}
