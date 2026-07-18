package com.myshop.api;

import com.myshop.api.dto.UserAddressQueryDTO;
import com.myshop.api.dto.UserGrowthAddDTO;
import com.myshop.api.dto.UserIdsDTO;
import com.myshop.api.dto.UserJoinCountDTO;
import com.myshop.api.dto.UserNotifyDTO;
import com.myshop.api.vo.UserAddressVO;
import com.myshop.api.vo.UserBriefVO;
import com.myshop.api.fallback.UserFeignFallbackFactory;
import com.myshop.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

import java.util.List;

@FeignClient(name = "simlect-user", contextId = "userFeignClient", path = "/internal/user",
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
