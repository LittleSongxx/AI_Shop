package com.myshop.biz;

import com.myshop.entity.po.UserNotification;
import com.myshop.entity.vo.PaginationResultVO;

import java.util.List;

public interface UserNotificationService {

    PaginationResultVO<UserNotification> loadPage(String userId, Integer pageNo, Integer readStatus);

    Integer countUnread(String userId);

    void markRead(String userId, String notificationId);

    void markAllRead(String userId);

    void delete(String userId, String notificationId);

    void clearAll(String userId);

    void send(String userId, String title, String content, String bizType, String bizId);

    void sendAsync(String userId, String title, String content, String bizType, String bizId);

    void batchInsert(List<UserNotification> notifications);

    UserNotification getPopupNotification(String userId);

    void clearPopupNotification(String userId, String notificationId);
}
