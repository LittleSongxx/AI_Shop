package com.aishop.biz;

import com.aishop.entity.query.RagSyncFailureQuery;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.entity.vo.RagSyncFailureVO;

public interface RagSyncFailureService {

    PaginationResultVO<RagSyncFailureVO> loadList(RagSyncFailureQuery query);

    void replay(Integer logId);

    void updateStatus(Integer logId, Integer status, String handleRemark);

    void dismissRedisSnapshot(String dataId, String dataType);
}
