package com.aishop.controller.admin;

import com.aishop.component.RedisComponent;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.api.vo.SignDateSyncResultVO;
import com.aishop.api.vo.SignRecordSyncResultVO;
import com.aishop.exception.BusinessException;
import com.aishop.biz.SignCalendarCacheService;
import com.aishop.biz.SignRecordSyncService;
import com.aishop.utils.AuthCookieHelper;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/admin/signRecord")
public class SignRecordAdminController extends com.aishop.controller.admin.ABaseController {

    @Resource
    private SignRecordSyncService signRecordSyncService;
    @Resource
    private SignCalendarCacheService signCalendarCacheService;
    @Resource
    private AuthCookieHelper authCookieHelper;
    @Resource
    private RedisComponent redisComponent;

    @PostMapping("/syncAllFromDb")
    public ResponseVO syncAllFromDb(Boolean force) {
        boolean overwrite = force == null || force;
        SignRecordSyncResultVO result = signRecordSyncService.syncAllFromDb(overwrite);
        return getSuccessResponseVO(result);
    }

    @PostMapping("/syncUserFromDb")
    public ResponseVO syncUserFromDb(String userId, Boolean force) {
        if (StringTools.isEmpty(userId)) {
            throw new BusinessException("userId 不能为空");
        }
        boolean overwrite = force != null && force;
        boolean synced = signRecordSyncService.syncUserFromDb(userId.trim(), overwrite);
        if (!synced) {
            throw new BusinessException("DB 无该用户签到记录，或 Redis 已有且未勾选强制覆盖");
        }
        return getSuccessResponseVO(null);
    }

    @PostMapping("/syncSignDatesFromDb")
    public ResponseVO syncSignDatesFromDb(String syncEndDate, String userId) {
        SignDateSyncResultVO result;
        if (StringTools.isEmpty(userId)) {
            result = signCalendarCacheService.syncAllSignDatesFromDb(syncEndDate, false);
        } else {
            result = signCalendarCacheService.syncSignDatesFromDb(userId.trim(), syncEndDate, false);
        }
        return getSuccessResponseVO(result);
    }

    @PostMapping("/forceRebuildToday")
    public ResponseVO forceRebuildToday(String userId, HttpServletRequest request) {
        String operator = resolveOperator(request);
        SignDateSyncResultVO result = signCalendarCacheService.forceRebuildToday(userId, operator);
        return getSuccessResponseVO(result);
    }

    private String resolveOperator(HttpServletRequest request) {
        if (request == null) {
            return "unknown";
        }
        String token = authCookieHelper.resolveAdminToken(request);
        if (StringTools.isEmpty(token)) {
            return "unknown";
        }
        Object admin = redisComponent.getLoginInfo4Admin(token);
        return admin == null ? "unknown" : String.valueOf(admin);
    }
}
