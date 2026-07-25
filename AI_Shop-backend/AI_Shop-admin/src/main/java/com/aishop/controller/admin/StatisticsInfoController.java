package com.aishop.controller.admin;

import com.aishop.entity.query.StatisticsInfoQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.StatisticsInfoService;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("statisticsInfoController")
@RequestMapping("/admin/statisticsInfo")
public class StatisticsInfoController extends com.aishop.controller.admin.ABaseController {

	@Resource
	private StatisticsInfoService statisticsInfoService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(StatisticsInfoQuery query) {
		query.setOrderBy(com.aishop.entity.query.SafeSort.of("statistics_date desc, data_type asc"));
		return getSuccessResponseVO(statisticsInfoService.findListByPage(query));
	}
}
