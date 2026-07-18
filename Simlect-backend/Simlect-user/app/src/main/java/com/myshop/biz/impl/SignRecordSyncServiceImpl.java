package com.myshop.biz.impl;

import com.myshop.component.RedisComponent;
import com.myshop.entity.po.UserSignRecord;
import com.myshop.entity.query.UserSignRecordQuery;
import com.myshop.entity.vo.SignRecordSyncResultVO;
import com.myshop.mappers.UserSignRecordMapper;
import com.myshop.biz.SignRecordSyncService;
import com.myshop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
@Slf4j
public class SignRecordSyncServiceImpl implements SignRecordSyncService {

    @Resource
    private UserSignRecordMapper<UserSignRecord, UserSignRecordQuery> userSignRecordMapper;
    @Resource
    private RedisComponent redisComponent;

    @Override
    public void ensureHashHydrated(String userId) {
        if (StringTools.isEmpty(userId)) {
            return;
        }
        if (redisComponent.hasSignHashSnapshot(userId)) {
            return;
        }
        syncUserFromDb(userId, false);
    }

    @Override
    public boolean syncUserFromDb(String userId, boolean force) {
        if (StringTools.isEmpty(userId)) {
            return false;
        }
        if (!force && redisComponent.hasSignHashSnapshot(userId)) {
            return false;
        }
        UserSignRecord record = userSignRecordMapper.selectByUserId(userId);
        if (record == null) {
            return false;
        }
        redisComponent.writeSignHash(
                userId,
                record.getContinuousDays(),
                record.getTotalSignDays(),
                record.getUsedCount());
        log.info("签到 Hash 已从 DB 回灌 userId={}, continuous={}, total={}, used={}",
                userId, record.getContinuousDays(), record.getTotalSignDays(), record.getUsedCount());
        return true;
    }

    @Override
    public SignRecordSyncResultVO syncAllFromDb(boolean force) {
        SignRecordSyncResultVO result = new SignRecordSyncResultVO();
        List<UserSignRecord> rows = userSignRecordMapper.selectList(new UserSignRecordQuery());
        if (rows == null || rows.isEmpty()) {
            return result;
        }
        result.setTotalInDb(rows.size());
        for (UserSignRecord row : rows) {
            if (row == null || StringTools.isEmpty(row.getUserId())) {
                continue;
            }
            if (!force && redisComponent.hasSignHashSnapshot(row.getUserId())) {
                result.setSkipped(result.getSkipped() + 1);
                continue;
            }
            redisComponent.writeSignHash(
                    row.getUserId(),
                    row.getContinuousDays(),
                    row.getTotalSignDays(),
                    row.getUsedCount());
            result.setSynced(result.getSynced() + 1);
        }
        log.info("签到 Hash 批量同步完成 force={}, total={}, synced={}, skipped={}",
                force, result.getTotalInDb(), result.getSynced(), result.getSkipped());
        return result;
    }

    @Override
    public void warmUpMissingOnStartup() {
        try {
            SignRecordSyncResultVO result = syncAllFromDb(false);
            log.info("签到 Hash 启动回灌完成 synced={}, skipped={}", result.getSynced(), result.getSkipped());
        } catch (Exception e) {
            log.error("签到 Hash 启动回灌失败，不影响服务启动", e);
        }
    }

    @EventListener(ApplicationReadyEvent.class)
    public void onApplicationReady() {
        warmUpMissingOnStartup();
    }
}
