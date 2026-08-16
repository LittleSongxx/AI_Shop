package com.aishop.mappers;

import com.aishop.entity.po.OrderInfo;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;

public interface OrderInfoMapper<T,P> extends BaseMapper<T,P> {

	 Integer updateByOrderId(@Param("bean") T t,@Param("orderId") String orderId);

	 Integer deleteByOrderId(@Param("orderId") String orderId);

	 T selectByOrderId(@Param("orderId") String orderId);

	 T selectByOrderIdForUpdate(@Param("orderId") String orderId);

	Integer markCommentEvaluatedIfNotEvaluated(@Param("orderId") String orderId, @Param("userId") String userId);

	Integer revertCommentStatusIfEvaluated(@Param("orderId") String orderId);

		@Select("""
				SELECT order_id AS orderId, user_id AS userId FROM order_info
				WHERE order_status = 1
				  AND order_time IS NOT NULL
				  AND order_time <= DATE_SUB(NOW(), INTERVAL #{delayHours} HOUR)
				ORDER BY order_time ASC
				LIMIT #{limit}
				""")
		List<OrderInfo> selectDelayedPaidOrders(
				@Param("delayHours") int delayHours,
				@Param("limit") int limit);

		@Select("""
				SELECT o.order_id AS orderId, o.user_id AS userId FROM order_info o
				JOIN (
				    SELECT order_id, MIN(record_time) AS shipped_at
				    FROM order_logistics_info_record
			    GROUP BY order_id
			) shipped ON shipped.order_id = o.order_id
			WHERE o.order_status IN (2, 7)
			  AND shipped.shipped_at <= DATE_SUB(NOW(), INTERVAL #{delayMinutes} MINUTE)
				ORDER BY shipped.shipped_at ASC
				LIMIT #{limit}
				""")
		List<OrderInfo> selectAutoReceivableOrders(
				@Param("delayMinutes") int delayMinutes,
				@Param("limit") int limit);

	}
