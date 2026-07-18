package com.myshop.controller;

import com.myshop.annotation.GlobalInterceptor;
import com.myshop.entity.vo.ResponseVO;
import com.myshop.biz.PayTradeRecordService;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RequestMapping("/payTrade")
@RestController
public class PayTradeRecordController extends ABaseController {

    @Resource
    private PayTradeRecordService payTradeRecordService;

    @PostMapping("/loadMyTrades")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO loadMyTrades(Integer pageNo) {
        return getSuccessResponseVO(
                payTradeRecordService.loadUserTrades(getTokenUserInfo().getUserId(), pageNo == null ? 1 : pageNo));
    }
}
