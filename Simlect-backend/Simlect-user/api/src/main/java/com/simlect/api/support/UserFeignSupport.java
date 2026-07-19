package com.simlect.api.support;

import com.simlect.api.UserFeignClient;
import com.simlect.api.dto.UserAddressQueryDTO;
import com.simlect.api.dto.UserGrowthAddDTO;
import com.simlect.api.dto.UserIdsDTO;
import com.simlect.api.dto.UserJoinCountDTO;
import com.simlect.api.dto.UserNotifyDTO;
import com.simlect.api.vo.UserAddressVO;
import com.simlect.api.vo.UserBriefVO;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

@Slf4j
@Component
public class UserFeignSupport {

    @Resource
    private UserFeignClient userFeignClient;
    @Resource
    private FeignResponseSupport feignResponseSupport;

    public UserAddressVO getAddress(String addressId, String userId) {
        return feignResponseSupport.call(
                () -> userFeignClient.getAddress(new UserAddressQueryDTO(addressId, userId)),
                "查询收货地址失败");
    }

    public void addGrowthOnPay(String userId, BigDecimal payAmount) {
        feignResponseSupport.run(
                () -> userFeignClient.addGrowthOnPay(new UserGrowthAddDTO(userId, payAmount)),
                "增加成长值失败");
    }

    public void sendNotifyAsync(String userId, String title, String content, String bizType, String bizId) {
        try {
            feignResponseSupport.run(
                    () -> userFeignClient.sendNotifyAsync(new UserNotifyDTO(userId, title, content, bizType, bizId)),
                    "发送站内通知失败");
        } catch (Exception e) {
            log.warn("发送站内通知降级跳过 userId={}, title={}, err={}", userId, title, e.getMessage());
        }
    }

    public List<String> listAllUserIds() {
        try {
            List<String> ids = feignResponseSupport.call(
                    () -> userFeignClient.listAllUserIds(),
                    "查询用户列表失败");
            return ids == null ? Collections.emptyList() : ids;
        } catch (Exception e) {
            log.warn("查询全部用户ID降级为空: {}", e.getMessage());
            return Collections.emptyList();
        }
    }

    public Map<String, UserBriefVO> mapBriefByUserIds(List<String> userIds) {
        if (userIds == null || userIds.isEmpty()) {
            return Collections.emptyMap();
        }
        try {
            List<UserBriefVO> list = feignResponseSupport.call(
                    () -> userFeignClient.listBriefByUserIds(new UserIdsDTO(userIds)),
                    "批量查询用户信息失败");
            if (list == null || list.isEmpty()) {
                return Collections.emptyMap();
            }
            return list.stream()
                    .filter(u -> u != null && u.getUserId() != null)
                    .collect(Collectors.toMap(UserBriefVO::getUserId, Function.identity(), (a, b) -> a));
        } catch (Exception e) {
            log.warn("批量查询用户信息降级为空: {}", e.getMessage());
            return new HashMap<>();
        }
    }

    public Integer countByJoinDate(String joinDateStart, String joinDateEnd) {
        Integer count = feignResponseSupport.call(
                () -> userFeignClient.countByJoinDate(new UserJoinCountDTO(joinDateStart, joinDateEnd)),
                "统计新用户失败");
        return count == null ? 0 : count;
    }
}
