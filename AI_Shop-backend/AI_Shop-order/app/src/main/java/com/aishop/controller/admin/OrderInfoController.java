package com.aishop.controller.admin;

import com.aishop.entity.query.OrderInfoQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.OrderInfoService;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("orderInfoController")
@RequestMapping("/admin/orderInfo")
public class OrderInfoController extends com.aishop.controller.admin.ABaseController{

	@Resource
	private OrderInfoService orderInfoService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(OrderInfoQuery query){
		return getSuccessResponseVO(orderInfoService.findListByPage(query));
	}

	@PostMapping("/getOrderInfoByOrderId")
	public ResponseVO getOrderInfoByOrderId(String orderId) {
		return getSuccessResponseVO(orderInfoService.getOrderInfoByOrderId(orderId));
	}

}
