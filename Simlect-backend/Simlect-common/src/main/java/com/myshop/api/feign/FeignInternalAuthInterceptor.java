package com.myshop.api.feign;

import com.myshop.constants.InternalApiHeaders;
import feign.RequestInterceptor;
import feign.RequestTemplate;
import org.springframework.util.StringUtils;

public class FeignInternalAuthInterceptor implements RequestInterceptor {

    private final String internalToken;

    public FeignInternalAuthInterceptor(String internalToken) {
        this.internalToken = internalToken;
    }

    @Override
    public void apply(RequestTemplate template) {
        if (StringUtils.hasText(internalToken)) {
            template.header(InternalApiHeaders.INTERNAL_TOKEN, internalToken);
        }
    }
}
