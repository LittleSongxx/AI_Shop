package com.aishop.biz;

import com.aishop.entity.dto.MemberLevelRewardConfigDTO;

public interface MemberLevelRewardConfigService {

    MemberLevelRewardConfigDTO getConfig();

    void saveConfig(MemberLevelRewardConfigDTO config);

    String resolveLevelCouponId(int levelCode);
}
