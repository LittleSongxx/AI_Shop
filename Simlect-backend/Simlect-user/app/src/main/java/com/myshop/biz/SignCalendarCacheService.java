package com.myshop.biz;

import com.myshop.entity.vo.SignDateSyncResultVO;

import java.util.List;

public interface SignCalendarCacheService {

    void ensureCalendarHydrated(String userId);

    SignDateSyncResultVO syncSignDatesFromDb(String userId, String syncEndDate, boolean forceIncludeToday);

    SignDateSyncResultVO syncAllSignDatesFromDb(String syncEndDate, boolean forceIncludeToday);

    SignDateSyncResultVO forceRebuildToday(String userId, String operatorAccount);

    int reconcileRecentHour();

    int initTodayBitmapForActiveUsers();

    void applyDetailsToRedis(String userId, List<com.myshop.entity.po.UserSignRecordDetail> details);
}
