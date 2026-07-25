package com.aishop.controller.admin;

import com.aishop.component.RedisComponent;
import com.aishop.component.UserTempBanService;
import com.aishop.api.enums.UserStatusEnum;
import com.aishop.entity.po.UserInfo;
import com.aishop.entity.query.UserInfoQuery;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.UserInfoService;
import jakarta.annotation.Resource;
import jakarta.validation.constraints.NotNull;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RequestMapping("/admin/user")
@RestController
public class UserController extends com.aishop.controller.admin.ABaseController{

    @Resource
    private UserInfoService userInfoService;
    @Resource
    private RedisComponent redisComponent;
    @Resource
    private UserTempBanService userTempBanService;

    // 加载所有用户
    @PostMapping("/loadUser")
    public ResponseVO loadUser(Integer pageNo, Integer pageSize, Integer status, String nickNameFuzzy){
        UserInfoQuery userInfoQuery = new UserInfoQuery();
        userInfoQuery.setPageNo(pageNo);
        userInfoQuery.setPageSize(pageSize);
        userInfoQuery.setStatus(status);
        userInfoQuery.setNickNameFuzzy(nickNameFuzzy);
        userInfoQuery.setOrderBy(com.aishop.entity.query.SafeSort.of("join_time desc"));
        PaginationResultVO<UserInfo> resultVO = userInfoService.findListByPage(userInfoQuery);
        return getSuccessResponseVO(resultVO);
    }

    // 改变用户状态
    @PostMapping("/changeStatus")
    public ResponseVO changeStatus(@NotNull String userId, @NotNull Integer status){
        // 根据userId查询用户
        UserInfo userInfo = userInfoService.getUserInfoByUserId(userId);
        userInfo.setStatus(status);
        userTempBanService.clearTempBanMark(userId);
        if (status.equals(UserStatusEnum.DISABLE.getStatus())) {
            redisComponent.cleanAllToken(userId);
        }
        userInfoService.updateUserInfoByUserId(userInfo, userId);
        return getSuccessResponseVO(null);
    }
}
