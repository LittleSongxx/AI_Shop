package com.aishop.controller;

import com.aishop.api.vo.ServiceHealthVO;
import com.aishop.entity.vo.ResponseVO;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/stock")
public class StockController extends ABaseController {

    @PostMapping("/health")
    public ResponseVO<ServiceHealthVO> health() {
        return getSuccessResponseVO(new ServiceHealthVO("aishop-stock", "UP"));
    }
}
