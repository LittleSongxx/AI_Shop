package com.simlect.controller;

import com.simlect.api.support.OrderFeignSupport;
import com.simlect.component.RedisComponent;
import com.simlect.component.SpringContext;
import com.simlect.constants.Constants;
import com.simlect.api.dto.PayOrderNotifyDTO;
import com.simlect.api.enums.PayChannelEnum;
import com.simlect.exception.BusinessException;
import com.simlect.biz.PayChannel;
import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;

@Slf4j
@RestController
@RequestMapping("/notify")
public class PayNotifyController {

    @Resource
    private OrderFeignSupport orderFeignSupport;
    @Resource
    private RedisComponent redisComponent;

    @PostMapping("/alipayNotify")
    public String alipayNotify(HttpServletRequest request) {
        Map<String, String> params = new HashMap<>();
        request.getParameterMap().forEach((k, v) -> {
            if (v != null && v.length > 0) {
                params.put(k, v[0]);
            }
        });
        PayChannel payChannel = (PayChannel) SpringContext.getBean(PayChannelEnum.ALIPAY_PC.getBeanName());
        PayOrderNotifyDTO notifyDTO;
        try {
            notifyDTO = payChannel.payNotify(params, null);
        } catch (Exception e) {
            log.error("支付宝回调验签失败", e);
            return "failure";
        }
        if (notifyDTO == null || notifyDTO.getPayOrderId() == null) {
            return "success";
        }
        String payOrderId = notifyDTO.getPayOrderId();
        String lockKey = Constants.REDIS_KEY_PAY_NOTIFY_LOCK + payOrderId;
        if (!redisComponent.setIfAbsent(lockKey, "1", 10, TimeUnit.MINUTES)) {
            log.info("支付宝回调重复，已忽略 payOrderId={}", payOrderId);
            return "success";
        }
        try {
            orderFeignSupport.paySuccess(notifyDTO);
        } catch (BusinessException e) {
            log.warn("支付宝回调业务处理失败 payOrderId={}, msg={}", payOrderId, e.getMessage());
            redisComponent.deleteCacheKey(lockKey);
            return "failure";
        } catch (Exception e) {
            log.error("支付宝回调处理异常 payOrderId={}", payOrderId, e);
            redisComponent.deleteCacheKey(lockKey);
            return "failure";
        }
        return "success";
    }
}
