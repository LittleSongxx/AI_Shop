package com.myshop.biz;

import com.myshop.entity.dto.SignRewardConfigDTO;

public interface SignRewardConfigService {

    SignRewardConfigDTO getConfig();

    void saveConfig(SignRewardConfigDTO config);

    SignRewardConfigDTO resolveActiveConfig();
}
