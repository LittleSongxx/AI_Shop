package com.myshop.biz;

import com.myshop.entity.dto.MemberLevelRewardConfigDTO;

public interface MemberLevelRewardConfigService {

    MemberLevelRewardConfigDTO getConfig();

    void saveConfig(MemberLevelRewardConfigDTO config);

    String resolveLevelCouponId(int levelCode);
}
