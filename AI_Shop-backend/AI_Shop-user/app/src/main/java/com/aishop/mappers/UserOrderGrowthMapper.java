package com.aishop.mappers;

import com.aishop.entity.po.UserOrderGrowth;
import org.apache.ibatis.annotations.Param;

public interface UserOrderGrowthMapper {

    Integer insert(@Param("bean") UserOrderGrowth bean);

    UserOrderGrowth selectByOrderId(@Param("orderId") String orderId);
}
