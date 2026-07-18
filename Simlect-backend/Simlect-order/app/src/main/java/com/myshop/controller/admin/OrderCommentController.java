package com.myshop.controller.admin;

import java.util.List;

import com.myshop.entity.query.OrderCommentQuery;
import com.myshop.entity.po.OrderComment;
import com.myshop.entity.vo.ResponseVO;
import com.myshop.biz.OrderCommentService;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.annotation.Resource;

@RestController("adminOrderCommentController")
@RequestMapping("/admin/orderComment")
public class OrderCommentController extends com.myshop.controller.admin.ABaseController{

	@Resource
	private OrderCommentService orderCommentService;

	@PostMapping("/loadDataList")
	public ResponseVO loadDataList(OrderCommentQuery query){
		return getSuccessResponseVO(orderCommentService.findListByPage(query));
	}

	@PostMapping("/add")
	public ResponseVO add(OrderComment bean) {
		orderCommentService.add(bean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/addBatch")
	public ResponseVO addBatch(@RequestBody List<OrderComment> listBean) {
		orderCommentService.addBatch(listBean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/addOrUpdateBatch")
	public ResponseVO addOrUpdateBatch(@RequestBody List<OrderComment> listBean) {
		orderCommentService.addBatch(listBean);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/getOrderCommentByOrderId")
	public ResponseVO getOrderCommentByOrderId(String orderId) {
		return getSuccessResponseVO(orderCommentService.getOrderCommentByOrderId(orderId));
	}

	@PostMapping("/updateOrderCommentByOrderId")
	public ResponseVO updateOrderCommentByOrderId(OrderComment bean,String orderId) {
		orderCommentService.updateOrderCommentByOrderId(bean,orderId);
		return getSuccessResponseVO(null);
	}

	@PostMapping("/deleteOrderCommentByOrderId")
	public ResponseVO deleteOrderCommentByOrderId(String orderId) {
		orderCommentService.deleteOrderCommentByOrderId(orderId);
		return getSuccessResponseVO(null);
	}
}
