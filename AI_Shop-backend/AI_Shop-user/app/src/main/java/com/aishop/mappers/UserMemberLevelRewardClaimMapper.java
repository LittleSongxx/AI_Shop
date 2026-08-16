package com.aishop.mappers;

import org.apache.ibatis.annotations.Param;

import java.util.List;

public interface UserMemberLevelRewardClaimMapper {

    Integer insertIgnore(
            @Param("userId") String userId,
            @Param("levelCode") Integer levelCode,
            @Param("userCouponId") String userCouponId,
            @Param("bonusGrowth") Integer bonusGrowth);

    List<Integer> selectClaimedLevels(@Param("userId") String userId);
}
