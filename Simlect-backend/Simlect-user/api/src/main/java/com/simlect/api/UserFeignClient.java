package com.simlect.api;

import com.simlect.api.dto.UserAddressQueryDTO;
import com.simlect.api.dto.UserGrowthAddDTO;
import com.simlect.api.dto.UserIdsDTO;
import com.simlect.api.dto.UserJoinCountDTO;
import com.simlect.api.dto.UserNotifyDTO;
import com.simlect.api.vo.UserAddressVO;
import com.simlect.api.vo.UserBriefVO;
import com.simlect.api.fallback.UserFeignFallbackFactory;
import com.simlect.entity.vo.ResponseVO;
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
