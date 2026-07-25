package com.aishop.controller.admin;

import com.aishop.entity.query.RagSyncFailureQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.RagSyncFailureService;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/admin/ragSyncFailure")
public class RagSyncFailureController extends com.aishop.controller.admin.ABaseController {

    @Resource
    private RagSyncFailureService ragSyncFailureService;

    @PostMapping("/loadDataList")
    public ResponseVO loadDataList(RagSyncFailureQuery query) {
        if (query.getOrderBy() == null) {
            query.setOrderBy(com.aishop.entity.query.SafeSort.of("log_id desc"));
        }
        return getSuccessResponseVO(ragSyncFailureService.loadList(query));
    }

    @PostMapping("/replay")
    public ResponseVO replay(Integer logId) {
        ragSyncFailureService.replay(logId);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/updateStatus")
    public ResponseVO updateStatus(Integer logId, Integer status, String handleRemark) {
        ragSyncFailureService.updateStatus(logId, status, handleRemark);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/dismissRedisSnapshot")
    public ResponseVO dismissRedisSnapshot(String dataId, String dataType) {
        ragSyncFailureService.dismissRedisSnapshot(dataId, dataType);
        return getSuccessResponseVO(null);
    }
}
