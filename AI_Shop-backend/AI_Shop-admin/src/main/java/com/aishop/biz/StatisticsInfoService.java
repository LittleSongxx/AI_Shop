package com.aishop.biz;

import java.util.List;

import com.aishop.entity.query.StatisticsInfoQuery;
import com.aishop.entity.po.StatisticsInfo;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.entity.vo.StatisticsDataVO;
import com.aishop.entity.vo.TodayDataVO;

public interface StatisticsInfoService {

	PaginationResultVO<StatisticsInfo> findListByPage(StatisticsInfoQuery param);

	List<TodayDataVO> getTodayData();

	List<StatisticsDataVO> loadWeeklyStatisticsData();

	void statistics(String startTime, String endTime);
}
