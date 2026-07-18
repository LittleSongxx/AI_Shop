package com.myshop.service;

import com.myshop.entity.dto.MqCompensationRecord;
import com.myshop.entity.po.MqCompensationLog;
import com.myshop.entity.query.MqCompensationLogQuery;
import com.myshop.entity.vo.PaginationResultVO;

public interface MqCompensationLogService {

    PaginationResultVO<MqCompensationLog> findListByPage(MqCompensationLogQuery query);

    MqCompensationLog getByLogId(Integer logId);

    void saveFromFailure(MqCompensationRecord record);

    void updateHandleStatus(Integer logId, Integer status, String handleRemark);

    void replay(Integer logId);

    int autoReplayPendingSendFailures(int batchSize, int maxRetryCount);
}
