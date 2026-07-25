package com.aishop.biz;

import com.aishop.api.dto.UserNotifyDTO;
import com.aishop.api.vo.UserAddressVO;
import com.aishop.api.vo.UserBriefVO;
import com.aishop.entity.po.UserAddress;
import com.aishop.entity.po.UserInfo;
import com.aishop.entity.query.UserAddressQuery;
import com.aishop.entity.query.UserInfoQuery;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.UserAddressMapper;
import com.aishop.mappers.UserInfoMapper;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.stream.Collectors;

@Service
public class UserInternalService {

    @Resource
    private UserAddressMapper<UserAddress, UserAddressQuery> userAddressMapper;
    @Resource
    private UserMemberProfileService userMemberProfileService;
    @Resource
    private UserNotificationService userNotificationService;
    @Resource
    private UserInfoMapper<UserInfo, UserInfoQuery> userInfoMapper;

    public UserAddressVO getAddress(String addressId, String userId) {
        if (StringTools.isEmpty(addressId) || StringTools.isEmpty(userId)) {
            return null;
        }
        UserAddress address = userAddressMapper.selectByAddressId(addressId);
        if (address == null || !userId.equals(address.getUserId())) {
            return null;
        }
        UserAddressVO vo = new UserAddressVO();
        vo.setAddressId(address.getAddressId());
        vo.setUserId(address.getUserId());
        vo.setAddress(address.getAddress());
        vo.setAddressee(address.getAddressee());
        vo.setPhone(address.getPhone());
        return vo;
    }

    public void addGrowthOnPay(String userId, BigDecimal payAmount) {
        if (StringTools.isEmpty(userId)) {
            throw new BusinessException("用户ID为空");
        }
        userMemberProfileService.addGrowthOnPay(userId, payAmount);
    }

    public void sendNotifyAsync(UserNotifyDTO dto) {
        if (dto == null) {
            return;
        }
        userNotificationService.sendAsync(
                dto.getUserId(), dto.getTitle(), dto.getContent(), dto.getBizType(), dto.getBizId());
    }

    public List<String> listAllUserIds() {
        List<String> ids = userInfoMapper.selectAllUserId();
        return ids == null ? Collections.emptyList() : ids;
    }

    public List<UserBriefVO> listBriefByUserIds(List<String> userIds) {
        if (userIds == null || userIds.isEmpty()) {
            return Collections.emptyList();
        }
        List<String> distinct = userIds.stream()
                .filter(id -> !StringTools.isEmpty(id))
                .distinct()
                .collect(Collectors.toList());
        if (distinct.isEmpty()) {
            return Collections.emptyList();
        }
        List<UserInfo> rows = userInfoMapper.selectBriefByUserIds(distinct);
        if (rows == null || rows.isEmpty()) {
            return Collections.emptyList();
        }
        List<UserBriefVO> result = new ArrayList<>(rows.size());
        for (UserInfo u : rows) {
            result.add(new UserBriefVO(u.getUserId(), u.getNickName(), u.getAvatar()));
        }
        return result;
    }

    public Integer countByJoinDate(String joinDateStart, String joinDateEnd) {
        if (StringTools.isEmpty(joinDateStart) || StringTools.isEmpty(joinDateEnd)) {
            return 0;
        }
        UserInfoQuery query = new UserInfoQuery();
        query.setJoinTimeStart(joinDateStart);
        query.setJoinTimeEnd(joinDateEnd);
        Integer count = userInfoMapper.selectCount(query);
        return count == null ? 0 : count;
    }
}
