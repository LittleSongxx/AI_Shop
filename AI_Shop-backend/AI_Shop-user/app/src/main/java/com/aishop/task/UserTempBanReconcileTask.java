package com.aishop.task;

import com.aishop.component.UserTempBanService;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Slf4j
@Component
@ConditionalOnProperty(name = "app.common-scheduling.enabled", havingValue = "true")
public class UserTempBanReconcileTask {

    @Resource
    private UserTempBanService userTempBanService;

    @Value("${user.temp-ban.reconcile-batch-size:100}")
    private int batchSize;

    @Scheduled(fixedDelayString = "${user.temp-ban.reconcile-interval-ms:60000}")
    public void reconcile() {
        try {
            int released = userTempBanService.reconcileExpiredBans(batchSize);
            if (released > 0) {
                log.info("临时封禁到期对账完成，解封 {} 个用户", released);
            }
        } catch (Exception e) {
            log.error("临时封禁到期对账失败", e);
        }
    }
}
