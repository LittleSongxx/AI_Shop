package com.myshop.biz;

import java.util.List;

import com.myshop.entity.query.OrderItemQuery;
import com.myshop.entity.po.OrderItem;
import com.myshop.entity.vo.PaginationResultVO;

public interface OrderItemService {

	List<OrderItem> findListByParam(OrderItemQuery param);

	Integer findCountByParam(OrderItemQuery param);

	PaginationResultVO<OrderItem> findListByPage(OrderItemQuery param);

	Integer add(OrderItem bean);

	Integer addBatch(List<OrderItem> listBean);

	Integer addOrUpdateBatch(List<OrderItem> listBean);

	Integer updateByParam(OrderItem bean,OrderItemQuery param);

	Integer deleteByParam(OrderItemQuery param);

	OrderItem getOrderItemByOrderItemId(String orderItemId);

	Integer updateOrderItemByOrderItemId(OrderItem bean,String orderItemId);

	Integer deleteOrderItemByOrderItemId(String orderItemId);

}
