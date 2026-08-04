package com.aishop.biz;

import com.aishop.api.dto.OrderGrowthEventDTO;
import com.aishop.entity.po.UserMemberProfile;
import com.aishop.entity.vo.MemberCenterVO;

public interface UserMemberProfileService {

    UserMemberProfile getOrInitProfile(String userId);

    MemberCenterVO getMemberCenter(String userId);

    void claimLevelReward(String userId, Integer levelCode);

    void addGrowthOnPay(String userId, java.math.BigDecimal payAmount);

    boolean applyOrderGrowth(OrderGrowthEventDTO event);

    void addGrowth(String userId, int points);
}
