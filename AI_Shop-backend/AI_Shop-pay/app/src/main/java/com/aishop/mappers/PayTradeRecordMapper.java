package com.aishop.mappers;

import org.apache.ibatis.annotations.Param;

import java.util.Date;

public interface PayTradeRecordMapper<T,P> extends BaseMapper<T,P> {

	Integer updateByTradeId(@Param("bean") T t, @Param("tradeId") String tradeId);
	Integer deleteByTradeId(@Param("tradeId") String tradeId);
	T selectByTradeId(@Param("tradeId") String tradeId);
	T selectByPayOrderId(@Param("payOrderId") String payOrderId);
	Integer updateSuccessIfPending(@Param("payOrderId") String payOrderId,
			@Param("channelOrderId") String channelOrderId,
			@Param("payTime") Date payTime);

}
