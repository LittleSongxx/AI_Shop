package com.myshop.biz;

import com.myshop.entity.vo.PaginationResultVO;
import com.myshop.entity.vo.UserBrowseProductVO;

public interface UserBrowseHistoryService {

    void recordBrowse(String userId, String productId);

    void enqueueRecordBrowse(String userId, String productId);

    PaginationResultVO<UserBrowseProductVO> loadBrowsePage(String userId, Integer pageNo);

    void clearBrowse(String userId);

    void removeBrowse(String userId, Long historyId);
}
