package com.aishop.controller.admin;

import com.aishop.api.support.OrderFeignSupport;
import com.aishop.api.support.SearchToolFeignSupport;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.StatisticsInfoService;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RequestMapping("/admin/tool")
@RestController
@Slf4j
public class ToolController extends com.aishop.controller.admin.ABaseController {

    @Resource
    private StatisticsInfoService statisticsInfoService;
    @Resource
    private SearchToolFeignSupport searchToolFeignSupport;
    @Resource
    private OrderFeignSupport orderFeignSupport;

    @PostMapping("/statistics")
    public ResponseVO statistics() {
        statisticsInfoService.statistics(null, null);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/productData")
    public ResponseVO productData() {
        searchToolFeignSupport.productData();
        return getSuccessResponseVO(null);
    }

    @PostMapping("/ragData")
    public ResponseVO ragData() {
        searchToolFeignSupport.ragData();
        return getSuccessResponseVO(null);
    }

    @PostMapping("/addAllOrderToDelayQueue")
    public ResponseVO addAllOrderToDelayQueue() {
        orderFeignSupport.addAllWaitPayToDelayQueue();
        return getSuccessResponseVO(null);
    }
}
