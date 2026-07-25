package com.aishop.controller.admin;

import com.aishop.entity.dto.MemberLevelRewardConfigDTO;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.MemberLevelRewardConfigService;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RequestMapping("/admin/memberLevelRewardConfig")
@RestController
public class MemberLevelRewardConfigController extends com.aishop.controller.admin.ABaseController {

    @Resource
    private MemberLevelRewardConfigService memberLevelRewardConfigService;

    @PostMapping("/getConfig")
    public ResponseVO getConfig() {
        return getSuccessResponseVO(memberLevelRewardConfigService.getConfig());
    }

    @PostMapping("/saveConfig")
    public ResponseVO saveConfig(MemberLevelRewardConfigDTO config) {
        memberLevelRewardConfigService.saveConfig(config);
        return getSuccessResponseVO(null);
    }
}
