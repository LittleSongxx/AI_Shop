package com.aishop.mappers;

import org.apache.ibatis.annotations.Param;

import java.util.Date;

public interface UserMemberProfileMapper<T, P> extends BaseMapper<T, P> {

    T selectByUserId(@Param("userId") String userId);

    Integer updateByUserId(@Param("bean") T bean, @Param("userId") String userId);

    Integer incrementGrowth(@Param("userId") String userId,
                            @Param("points") int points,
                            @Param("updateTime") Date updateTime);
}
