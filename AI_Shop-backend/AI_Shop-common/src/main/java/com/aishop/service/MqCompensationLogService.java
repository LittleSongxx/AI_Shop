package com.aishop.service;

import com.aishop.entity.dto.MqCompensationRecord;
import com.aishop.entity.po.MqCompensationLog;
import com.aishop.entity.query.MqCompensationLogQuery;
import com.aishop.entity.vo.PaginationResultVO;

public interface MqCompensationLogService {

    PaginationResultVO<MqCompensationLog> findListByPage(MqCompensationLogQuery query);

    MqCompensationLog getByLogId(Integer logId);

    void saveFromFailure(MqCompensationRecord record);

    void updateHandleStatus(Integer logId, Integer status, String handleRemark);

    void replay(Integer logId);

    int autoReplayPendingSendFailures(int batchSize, int maxRetryCount);
}
