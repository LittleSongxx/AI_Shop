package com.aishop.task;

import com.aishop.entity.enums.DateTimePatternEnum;
import com.aishop.biz.StatisticsInfoService;
import com.aishop.utils.DateUtil;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
@Slf4j
public class AutoDataTask {

    @Resource
    private StatisticsInfoService statisticsInfoService;

    // 每天的凌晨一点自动统计昨天的数据
    @Scheduled(cron = "0 0 1 * * ?")
    @PostConstruct
    public void autoCountYesterdayData(){
        // 获取系统当前时间yyyyMMddHHss格式
        String start = DateUtil.getTimeOnParttern(1, DateTimePatternEnum.YYYY_MM_DD.getPattern()) + " 01:00:00";
        String end = DateUtil.getTimeOnParttern(0, DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern());
        statisticsInfoService.statistics(start,end);
        log.info("同步统计数据完成");
    }
}
