package com.myshop.api.fallback;

import com.myshop.api.UserFeignClient;
import com.myshop.api.dto.UserAddressQueryDTO;
import com.myshop.api.dto.UserGrowthAddDTO;
import com.myshop.api.dto.UserIdsDTO;
import com.myshop.api.dto.UserJoinCountDTO;
import com.myshop.api.dto.UserNotifyDTO;
import com.myshop.api.support.FeignFallbackResponses;
import com.myshop.api.vo.UserAddressVO;
import com.myshop.api.vo.UserBriefVO;
import com.myshop.entity.vo.ResponseVO;
import lombok.extern.slf4j.Slf4j;
import org.springframework.cloud.openfeign.FallbackFactory;
import org.springframework.stereotype.Component;

import java.util.List;

@Slf4j
@Component
public class UserFeignFallbackFactory implements FallbackFactory<UserFeignClient> {

    @Override
    public UserFeignClient create(Throwable cause) {
        log.warn("User Feign fallback: {}", cause == null ? "unknown" : cause.toString());
        return new UserFeignClient() {
            @Override
            public ResponseVO<UserAddressVO> getAddress(UserAddressQueryDTO dto) {
                return FeignFallbackResponses.unavailable("用户服务");
            }

            @Override
            public ResponseVO<Void> addGrowthOnPay(UserGrowthAddDTO dto) {
                return FeignFallbackResponses.unavailable("用户服务");
            }

            @Override
            public ResponseVO<Void> sendNotifyAsync(UserNotifyDTO dto) {
                return FeignFallbackResponses.unavailable("用户服务");
            }

            @Override
            public ResponseVO<List<String>> listAllUserIds() {
                return FeignFallbackResponses.unavailable("用户服务");
            }

            @Override
            public ResponseVO<List<UserBriefVO>> listBriefByUserIds(UserIdsDTO dto) {
                return FeignFallbackResponses.unavailable("用户服务");
            }

            @Override
            public ResponseVO<Integer> countByJoinDate(UserJoinCountDTO dto) {
                return FeignFallbackResponses.unavailable("用户服务");
            }
        };
    }
}
