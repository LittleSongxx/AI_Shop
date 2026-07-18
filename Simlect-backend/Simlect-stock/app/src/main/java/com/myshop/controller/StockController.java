package com.myshop.controller;

import com.myshop.api.vo.ServiceHealthVO;
import com.myshop.entity.vo.ResponseVO;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/stock")
public class StockController extends ABaseController {

    @PostMapping("/health")
    public ResponseVO<ServiceHealthVO> health() {
        return getSuccessResponseVO(new ServiceHealthVO("simlect-stock", "UP"));
    }
}
