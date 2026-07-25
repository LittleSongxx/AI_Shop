package com.aishop.controller.admin;

import com.aishop.entity.query.OrderCommentQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.OrderCommentService;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("adminOrderCommentController")
@RequestMapping("/admin/orderComment")
public class OrderCommentController extends com.aishop.controller.admin.ABaseController{

	@Resource
	private OrderCommentService orderCommentService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(OrderCommentQuery query){
		return getSuccessResponseVO(orderCommentService.findListByPage(query));
	}

	@PostMapping("/getOrderCommentByOrderId")
	public ResponseVO getOrderCommentByOrderId(String orderId) {
		return getSuccessResponseVO(orderCommentService.getOrderCommentByOrderId(orderId));
	}

}
