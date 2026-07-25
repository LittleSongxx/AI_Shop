package com.aishop.biz;

import com.aishop.entity.dto.SignRewardConfigDTO;

public interface SignRewardConfigService {

    SignRewardConfigDTO getConfig();

    void saveConfig(SignRewardConfigDTO config);

    SignRewardConfigDTO resolveActiveConfig();
}
