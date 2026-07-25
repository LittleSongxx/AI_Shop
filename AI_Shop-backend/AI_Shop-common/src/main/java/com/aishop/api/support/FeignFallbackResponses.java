package com.aishop.api.support;

import com.aishop.entity.enums.ResponseCodeEnum;
import com.aishop.entity.vo.ResponseVO;
import org.slf4j.Logger;

public final class FeignFallbackResponses {

    private FeignFallbackResponses() {
    }

    public static <T> ResponseVO<T> unavailable(String serviceLabel) {
        return unavailable(null, serviceLabel, null);
    }

    public static <T> ResponseVO<T> unavailable(Logger log, String serviceLabel, Throwable cause) {
        if (log != null) {
            log.warn("{} 触发降级: {}", serviceLabel, cause == null ? "unknown" : cause.toString());
        }
        ResponseVO<T> vo = new ResponseVO<>();
        vo.setStatus("error");
        vo.setCode(ResponseCodeEnum.CODE_500.getCode());
        vo.setInfo(serviceLabel + "暂不可用，请稍后重试");
        vo.setData(null);
        return vo;
    }
}
