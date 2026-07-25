package com.aishop.api;

import com.aishop.api.dto.UserAddressQueryDTO;
import com.aishop.api.dto.UserGrowthAddDTO;
import com.aishop.api.dto.UserIdsDTO;
import com.aishop.api.dto.UserJoinCountDTO;
import com.aishop.api.dto.UserNotifyDTO;
import com.aishop.api.vo.UserAddressVO;
import com.aishop.api.vo.UserBriefVO;
import com.aishop.api.fallback.UserFeignFallbackFactory;
import com.aishop.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

import java.util.List;

@FeignClient(name = "aishop-user", contextId = "userFeignClient", path = "/internal/user",
        fallbackFactory = UserFeignFallbackFactory.class)
public interface UserFeignClient {

    @PostMapping("/address/get")
    ResponseVO<UserAddressVO> getAddress(@RequestBody UserAddressQueryDTO dto);

    @PostMapping("/member/addGrowthOnPay")
    ResponseVO<Void> addGrowthOnPay(@RequestBody UserGrowthAddDTO dto);

    @PostMapping("/notify/sendAsync")
    ResponseVO<Void> sendNotifyAsync(@RequestBody UserNotifyDTO dto);

    @PostMapping("/listAllUserIds")
    ResponseVO<List<String>> listAllUserIds();

    @PostMapping("/listBriefByUserIds")
    ResponseVO<List<UserBriefVO>> listBriefByUserIds(@RequestBody UserIdsDTO dto);

    @PostMapping("/countByJoinDate")
    ResponseVO<Integer> countByJoinDate(@RequestBody UserJoinCountDTO dto);
}
