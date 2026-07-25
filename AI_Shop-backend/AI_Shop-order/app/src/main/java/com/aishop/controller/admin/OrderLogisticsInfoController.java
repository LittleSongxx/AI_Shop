package com.aishop.controller.admin;

import com.aishop.entity.query.OrderLogisticsInfoQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.OrderLogisticsInfoService;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("orderLogisticsInfoController")
@RequestMapping("/admin/orderLogisticsInfo")
public class OrderLogisticsInfoController extends com.aishop.controller.admin.ABaseController{

	@Resource
	private OrderLogisticsInfoService orderLogisticsInfoService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(OrderLogisticsInfoQuery query){
		return getSuccessResponseVO(orderLogisticsInfoService.findListByPage(query));
	}

	@PostMapping("/getOrderLogisticsInfoByOrderId")
	public ResponseVO getOrderLogisticsInfoByOrderId(String orderId) {
		return getSuccessResponseVO(orderLogisticsInfoService.getOrderLogisticsInfoByOrderId(orderId));
	}

}
