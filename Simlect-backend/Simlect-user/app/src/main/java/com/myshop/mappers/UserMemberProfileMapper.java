package com.myshop.mappers;

import org.apache.ibatis.annotations.Param;

public interface UserMemberProfileMapper<T, P> extends BaseMapper<T, P> {

    T selectByUserId(@Param("userId") String userId);

    Integer updateByUserId(@Param("bean") T bean, @Param("userId") String userId);
}
