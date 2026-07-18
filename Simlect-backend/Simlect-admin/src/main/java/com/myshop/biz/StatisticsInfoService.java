package com.myshop.biz;

import java.util.List;

import com.myshop.entity.query.StatisticsInfoQuery;
import com.myshop.entity.po.StatisticsInfo;
import com.myshop.entity.vo.PaginationResultVO;
import com.myshop.entity.vo.StatisticsDataVO;
import com.myshop.entity.vo.TodayDataVO;

public interface StatisticsInfoService {

	PaginationResultVO<StatisticsInfo> findListByPage(StatisticsInfoQuery param);

	List<TodayDataVO> getTodayData();

	List<StatisticsDataVO> loadWeeklyStatisticsData();

	void statistics(String startTime, String endTime);
}
