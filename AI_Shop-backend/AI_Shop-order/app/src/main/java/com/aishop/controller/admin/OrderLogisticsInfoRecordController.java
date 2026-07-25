package com.aishop.controller.admin;

import com.aishop.entity.query.OrderLogisticsInfoRecordQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.OrderLogisticsInfoRecordService;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("orderLogisticsInfoRecordController")
@RequestMapping("/admin/orderLogisticsInfoRecord")
public class OrderLogisticsInfoRecordController extends com.aishop.controller.admin.ABaseController{

	@Resource
	private OrderLogisticsInfoRecordService orderLogisticsInfoRecordService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(OrderLogisticsInfoRecordQuery query){
		return getSuccessResponseVO(orderLogisticsInfoRecordService.findListByPage(query));
	}

	@PostMapping("/getOrderLogisticsInfoRecordByRecordId")
	public ResponseVO getOrderLogisticsInfoRecordByRecordId(Integer recordId) {
		return getSuccessResponseVO(orderLogisticsInfoRecordService.getOrderLogisticsInfoRecordByRecordId(recordId));
	}

}
