package com.aishop.mappers;

import com.aishop.entity.po.ProductItem;
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
			SELECT * FROM order_info
			WHERE order_status = 1
			  AND order_time IS NOT NULL
			  AND order_time <= DATE_SUB(NOW(), INTERVAL #{delayHours} HOUR)
			ORDER BY order_time ASC
			LIMIT #{limit}
			""")
	List<T> selectDelayedPaidOrders(@Param("delayHours") int delayHours, @Param("limit") int limit);

}
