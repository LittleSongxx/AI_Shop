package com.aishop.mappers;

import org.apache.ibatis.annotations.Param;

import java.util.List;

public interface OrderItemMapper<T,P> extends BaseMapper<T,P> {

	 Integer updateByOrderItemId(@Param("bean") T t,@Param("orderItemId") String orderItemId);

	 Integer deleteByOrderItemId(@Param("orderItemId") String orderItemId);

	 T selectByOrderItemId(@Param("orderItemId") String orderItemId);

	 T selectByOrderItemIdForUpdate(@Param("orderItemId") String orderItemId);

	 Integer countNormalByOrderId(@Param("orderId") String orderId);

	 List<T> selectByOrderIds(@Param("orderIds") List<String> orderIds);

	 Integer countPriorSuccessfulPurchases(
			 @Param("userId") String userId,
			 @Param("productId") String productId,
			 @Param("excludedOrderIds") List<String> excludedOrderIds,
			 @Param("successfulStatuses") List<Integer> successfulStatuses);

}
