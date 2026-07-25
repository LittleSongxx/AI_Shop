package com.aishop.controller.admin;

import com.aishop.component.RedisComponent;
import com.aishop.constants.Constants;
import com.aishop.entity.dto.LogisticsSendDTO;
import com.aishop.entity.enums.PromptTypeEnum;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import jakarta.validation.constraints.NotEmpty;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RequestMapping("/admin/setting")
@RestController
public class SettingController extends com.aishop.controller.admin.ABaseController{

    @Resource
    private RedisComponent redisComponent;

    // 保存系统发货地址
    @PostMapping("/saveLogistics")
    public ResponseVO saveLogistics(LogisticsSendDTO logisticsSendDTO){
        redisComponent.saveLogistics(logisticsSendDTO);
        return getSuccessResponseVO(null);
    }

    // 获取发货地址
    @PostMapping("/getLogistics")
    public ResponseVO getLogistics(){
        return getSuccessResponseVO(redisComponent.getLogisticsInfo());
    }

    // 加载提示词分类
    @PostMapping("/loadPromptList")
    public ResponseVO loadPromptList(){
        return getSuccessResponseVO(PromptTypeEnum.getPrompts());
    }

    // 加载提示词内容
    @PostMapping("/getPromptDetail")
    public ResponseVO getPromptDetail(@NotEmpty String key){
        String prompt = redisComponent.getPrompt(key);
        if (StringTools.isEmpty(prompt)) {
            prompt = PromptTypeEnum.getByKey(key).getPrompt();
        }
        return getSuccessResponseVO(prompt);
    }

    // 保存提示词内容
    @PostMapping("/savePrompt")
    public ResponseVO savePrompt(@NotEmpty String key, String prompt){
        redisComponent.savePrompt(key, prompt);
        return getSuccessResponseVO(null);
    }

    // 清除提示词
    @PostMapping("/cleanPromptCache")
    public ResponseVO cleanPromptCache(@NotEmpty String key){
        redisComponent.cleanPrompt(key);
        return getSuccessResponseVO(null);
    }
}
