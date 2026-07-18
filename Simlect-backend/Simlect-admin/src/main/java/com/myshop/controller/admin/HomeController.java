package com.myshop.controller.admin;

import com.myshop.api.support.ProductFeignSupport;
import com.myshop.constants.Constants;
import com.myshop.entity.vo.PaginationResultVO;
import com.myshop.entity.vo.ProductSkuListVO;
import com.myshop.entity.vo.ResponseVO;
import com.myshop.entity.vo.StatisticsDataVO;
import com.myshop.entity.vo.TodayDataVO;
import com.myshop.biz.impl.StatisticsInfoServiceImpl;
import jakarta.annotation.Resource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

@RequestMapping("/admin/home")
@RestController
public class HomeController extends com.myshop.controller.admin.ABaseController {

    private static final Logger log = LoggerFactory.getLogger(HomeController.class);

    @Resource
    private StatisticsInfoServiceImpl statisticsInfoService;

    @Resource
    private ProductFeignSupport productFeignSupport;

    @PostMapping("/loadLessStockProduct")
    public ResponseVO loadLessStockProduct(Integer pageNo, Integer pageSize) {
        int no = pageNo == null ? 1 : pageNo;
        int size = pageSize == null ? 15 : pageSize;
        PaginationResultVO<ProductSkuListVO> result = productFeignSupport.lessStockSkuPage(no, size, Constants.LENGTH_10);
        return getSuccessResponseVO(result);
    }

    @PostMapping("/getTodayData")
    public ResponseVO getTodayData() {
        try {
            return getSuccessResponseVO(statisticsInfoService.getTodayData());
        } catch (Exception e) {
            log.warn("getTodayData degrade: {}", e.getMessage());
            List<TodayDataVO> zeros = new ArrayList<>();
            zeros.add(new TodayDataVO("orderAmount", BigDecimal.ZERO, BigDecimal.ZERO));
            zeros.add(new TodayDataVO("orderCount", BigDecimal.ZERO, BigDecimal.ZERO));
            zeros.add(new TodayDataVO("userCount", BigDecimal.ZERO, BigDecimal.ZERO));
            zeros.add(new TodayDataVO("refundAmount", BigDecimal.ZERO, BigDecimal.ZERO));
            return getSuccessResponseVO(zeros);
        }
    }

    @PostMapping("/loadWeeklyStatisticsData")
    public ResponseVO loadWeeklyStatisticsData() {
        try {
            List<StatisticsDataVO> list = statisticsInfoService.loadWeeklyStatisticsData();
            return getSuccessResponseVO(list);
        } catch (Exception e) {
            log.warn("loadWeeklyStatisticsData degrade: {}", e.getMessage());
            return getSuccessResponseVO(Collections.emptyList());
        }
    }
}
