package com.myshop.controller.admin;

import java.util.List;

import com.myshop.entity.query.OrderInfoQuery;
import com.myshop.entity.po.OrderInfo;
import com.myshop.entity.vo.ResponseVO;
import com.myshop.biz.OrderInfoService;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("orderInfoController")
@RequestMapping("/admin/orderInfo")
public class OrderInfoController extends com.myshop.controller.admin.ABaseController{

	@Resource
	private OrderInfoService orderInfoService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(OrderInfoQuery query){
		return getSuccessResponseVO(orderInfoService.findListByPage(query));
	}

	@PostMapping("/add")
	public ResponseVO add(OrderInfo bean) {
		orderInfoService.add(bean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/addBatch")
	public ResponseVO addBatch(@RequestBody List<OrderInfo> listBean) {
		orderInfoService.addBatch(listBean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/addOrUpdateBatch")
	public ResponseVO addOrUpdateBatch(@RequestBody List<OrderInfo> listBean) {
		orderInfoService.addBatch(listBean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/getOrderInfoByOrderId")
	public ResponseVO getOrderInfoByOrderId(String orderId) {
		return getSuccessResponseVO(orderInfoService.getOrderInfoByOrderId(orderId));
	}

	@PostMapping("/updateOrderInfoByOrderId")
	public ResponseVO updateOrderInfoByOrderId(OrderInfo bean,String orderId) {
		orderInfoService.updateOrderInfoByOrderId(bean,orderId);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/deleteOrderInfoByOrderId")
	public ResponseVO deleteOrderInfoByOrderId(String orderId) {
		orderInfoService.deleteOrderInfoByOrderId(orderId);
		return getSuccessResponseVO(null);
	}
}
