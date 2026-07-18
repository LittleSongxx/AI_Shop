package com.myshop.biz;

import com.myshop.entity.query.RagSyncFailureQuery;
import com.myshop.entity.vo.PaginationResultVO;
import com.myshop.entity.vo.RagSyncFailureVO;

public interface RagSyncFailureService {

    PaginationResultVO<RagSyncFailureVO> loadList(RagSyncFailureQuery query);

    void replay(Integer logId);

    void updateStatus(Integer logId, Integer status, String handleRemark);

    void dismissRedisSnapshot(String dataId, String dataType);
}
