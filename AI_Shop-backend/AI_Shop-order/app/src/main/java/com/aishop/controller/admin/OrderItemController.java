package com.aishop.controller.admin;

import com.aishop.entity.query.OrderItemQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.OrderItemService;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("orderItemController")
@RequestMapping("/admin/orderItem")
public class OrderItemController extends com.aishop.controller.admin.ABaseController{

	@Resource
	private OrderItemService orderItemService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(OrderItemQuery query){
		return getSuccessResponseVO(orderItemService.findListByPage(query));
	}

	@PostMapping("/getOrderItemByOrderItemId")
	public ResponseVO getOrderItemByOrderItemId(String orderItemId) {
		return getSuccessResponseVO(orderItemService.getOrderItemByOrderItemId(orderItemId));
	}

}
