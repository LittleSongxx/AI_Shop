package com.aishop.biz;

import com.aishop.api.dto.SignRecordMessageDTO;
import com.aishop.entity.po.UserSignRecord;
import com.aishop.entity.po.UserSignRecordDetail;
import com.aishop.entity.query.UserSignRecordDetailQuery;
import com.aishop.entity.query.UserSignRecordQuery;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.UserSignRecordDetailMapper;
import com.aishop.mappers.UserSignRecordMapper;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Date;

@Service
public class SignEventPersistenceService {

    @Resource
    private UserSignRecordMapper<UserSignRecord, UserSignRecordQuery> userSignRecordMapper;
    @Resource
    private UserSignRecordDetailMapper<UserSignRecordDetail, UserSignRecordDetailQuery>
            userSignRecordDetailMapper;
    @Resource
    private UserMemberProfileService userMemberProfileService;

    @Transactional(rollbackFor = Exception.class)
    public boolean persist(SignRecordMessageDTO message, int growthPoints) {
        if (message == null
                || StringTools.isEmpty(message.getUserId())
                || StringTools.isEmpty(message.getSignDate())
                || message.getSignDate().length() != 8
                || growthPoints <= 0) {
            throw new BusinessException("签到持久化参数不完整");
        }

        UserSignRecordDetail detail = new UserSignRecordDetail();
        detail.setUserId(message.getUserId());
        detail.setSignDate(message.getSignDate());
        detail.setSignType(message.getSignType() == null ? 0 : message.getSignType());
        detail.setCreateTime(new Date());
        Integer inserted = userSignRecordDetailMapper.insertIgnore(detail);
        if (inserted == null || inserted != 1) {
            return false;
        }

        UserSignRecord record = new UserSignRecord();
        record.setUserId(message.getUserId());
        record.setContinuousDays(message.getContinuousDays());
        record.setTotalSignDays(message.getTotalSignDays());
        record.setUsedCount(message.getUsedCount());
        userSignRecordMapper.insertOrUpdate(record);
        userMemberProfileService.addGrowth(message.getUserId(), growthPoints);
        return true;
    }
}
