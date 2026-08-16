package com.aishop.mappers;

import org.apache.ibatis.annotations.Param;

public interface UserCouponMapper<T,P> extends BaseMapper<T,P> {

	 Integer insertGranted(@Param("bean") T t);

	 Integer updateByUserCouponId(@Param("bean") T t,@Param("userCouponId") String userCouponId);

	 Integer deleteByUserCouponId(@Param("userCouponId") String userCouponId);

	 T selectByUserCouponId(@Param("userCouponId") String userCouponId);

	java.util.List<java.util.Map<String, Object>> selectExpiringUnused(@Param("limit") int limit);

}
