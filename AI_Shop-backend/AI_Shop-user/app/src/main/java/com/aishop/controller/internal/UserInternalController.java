package com.aishop.controller.internal;

import com.aishop.api.dto.UserAddressQueryDTO;
import com.aishop.api.dto.UserGrowthAddDTO;
import com.aishop.api.dto.UserIdsDTO;
import com.aishop.api.dto.UserJoinCountDTO;
import com.aishop.api.dto.UserNotifyDTO;
import com.aishop.api.vo.UserAddressVO;
import com.aishop.api.vo.UserBriefVO;
import com.aishop.biz.UserInternalService;
import com.aishop.controller.ABaseController;
import com.aishop.entity.vo.ResponseVO;
import jakarta.annotation.Resource;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Collections;
import java.util.List;

@RestController
@RequestMapping("/internal/user")
public class UserInternalController extends ABaseController {

    @Resource
    private UserInternalService userInternalService;

    @PostMapping("/address/get")
    public ResponseVO<UserAddressVO> getAddress(@Valid @RequestBody UserAddressQueryDTO dto) {
        return getSuccessResponseVO(userInternalService.getAddress(dto.getAddressId(), dto.getUserId()));
    }

    @PostMapping("/member/addGrowthOnPay")
    public ResponseVO<Void> addGrowthOnPay(@Valid @RequestBody UserGrowthAddDTO dto) {
        userInternalService.addGrowthOnPay(dto.getUserId(), dto.getPayAmount());
        return getSuccessResponseVO(null);
    }

    @PostMapping("/notify/sendAsync")
    public ResponseVO<Void> sendNotifyAsync(@RequestBody UserNotifyDTO dto) {
        userInternalService.sendNotifyAsync(dto);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/listAllUserIds")
    public ResponseVO<List<String>> listAllUserIds() {
        return getSuccessResponseVO(userInternalService.listAllUserIds());
    }

    @PostMapping("/listBriefByUserIds")
    public ResponseVO<List<UserBriefVO>> listBriefByUserIds(@RequestBody UserIdsDTO dto) {
        List<String> ids = dto == null ? Collections.emptyList() : dto.getUserIds();
        return getSuccessResponseVO(userInternalService.listBriefByUserIds(ids));
    }

    @PostMapping("/countByJoinDate")
    public ResponseVO<Integer> countByJoinDate(@RequestBody UserJoinCountDTO dto) {
        String start = dto == null ? null : dto.getJoinDateStart();
        String end = dto == null ? null : dto.getJoinDateEnd();
        return getSuccessResponseVO(userInternalService.countByJoinDate(start, end));
    }
}
