package com.aishop.biz.impl;

import com.aishop.api.dto.ProductSnapshotBatchVO;
import com.aishop.api.support.ProductFeignSupport;
import com.aishop.api.vo.ProductInfoSnapshotVO;
import com.aishop.component.RedisComponent;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.ReliableMessageSender;
import com.aishop.support.MqIdempotencyKeys;
import com.aishop.api.dto.BrowseHistoryMessageDTO;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.enums.PageSize;
import com.aishop.entity.po.UserBrowseHistory;
import com.aishop.entity.query.SimplePage;
import com.aishop.entity.query.UserBrowseHistoryQuery;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.api.vo.UserBrowseProductVO;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.UserBrowseHistoryMapper;
import com.aishop.biz.UserBrowseHistoryService;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Service("userBrowseHistoryService")
@Slf4j
public class UserBrowseHistoryServiceImpl implements UserBrowseHistoryService {

    @Resource
    private UserBrowseHistoryMapper<UserBrowseHistory, UserBrowseHistoryQuery> userBrowseHistoryMapper;
    @Resource
    private ProductFeignSupport productFeignSupport;
    @Resource
    private RedisComponent redisComponent;
    @Resource
    private ReliableMessageSender reliableMessageSender;

    @Override
    public void enqueueRecordBrowse(String userId, String productId) {
        if (StringTools.isEmpty(userId) || StringTools.isEmpty(productId)) {
            return;
        }
        redisComponent.recordBrowseRecent(userId, productId);
        BrowseHistoryMessageDTO message = new BrowseHistoryMessageDTO();
        message.setUserId(userId);
        message.setProductId(productId);
        long browseTime = System.currentTimeMillis();
        message.setBrowseTime(browseTime);
        reliableMessageSender.sendMessage(
                RabbitMQConfig.BROWSE_EXCHANGE,
                RabbitMQConfig.BROWSE_RECORD_KEY,
                message,
                MqIdempotencyKeys.browseRecord(userId, productId, browseTime),
                MessageReliabilityLevelEnum.HIGH);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void recordBrowse(String userId, String productId) {
        if (StringTools.isEmpty(userId) || StringTools.isEmpty(productId)) {
            return;
        }
        UserBrowseHistoryQuery query = new UserBrowseHistoryQuery();
        query.setUserId(userId);
        query.setProductId(productId);
        query.setOrderBy(com.aishop.entity.query.SafeSort.of("browse_time desc"));
        List<UserBrowseHistory> exists = userBrowseHistoryMapper.selectList(query);
        Date now = new Date();
        if (!exists.isEmpty()) {
            UserBrowseHistory update = new UserBrowseHistory();
            update.setBrowseTime(now);
            userBrowseHistoryMapper.updateByHistoryId(update, exists.get(0).getHistoryId());
            return;
        }
        UserBrowseHistory history = new UserBrowseHistory();
        history.setUserId(userId);
        history.setProductId(productId);
        history.setBrowseTime(now);
        userBrowseHistoryMapper.insert(history);
    }

    @Override
    public PaginationResultVO<UserBrowseProductVO> loadBrowsePage(String userId, Integer pageNo) {
        UserBrowseHistoryQuery query = new UserBrowseHistoryQuery();
        query.setUserId(userId);
        query.setPageNo(pageNo);
        query.setOrderBy(com.aishop.entity.query.SafeSort.of("browse_time desc"));
        int count = userBrowseHistoryMapper.selectCount(query);
        int pageSize = PageSize.SIZE15.getSize();
        SimplePage page = new SimplePage(pageNo, count, pageSize);
        query.setSimplePage(page);
        List<UserBrowseHistory> histories = userBrowseHistoryMapper.selectList(query);
        List<UserBrowseProductVO> voList = new ArrayList<>();
        if (!histories.isEmpty()) {
            List<String> productIds = histories.stream().map(UserBrowseHistory::getProductId).collect(Collectors.toList());
            ProductSnapshotBatchVO batch = productFeignSupport.snapshotBatch(productIds);
            Map<String, ProductInfoSnapshotVO> productMap = productFeignSupport.toProductInfoMap(batch);
            for (UserBrowseHistory history : histories) {
                UserBrowseProductVO vo = new UserBrowseProductVO();
                vo.setHistoryId(history.getHistoryId());
                vo.setProductId(history.getProductId());
                vo.setBrowseTime(history.getBrowseTime());
                ProductInfoSnapshotVO product = productMap.get(history.getProductId());
                if (product != null) {
                    vo.setProductName(product.getProductName());
                    vo.setCover(resolveCover(product.getCover()));
                    vo.setStatus(product.getStatus());
                    vo.setMinPrice(product.getMinPrice());
                }
                voList.add(vo);
            }
        }
        return new PaginationResultVO<>(count, page.getPageSize(), page.getPageNo(), page.getPageTotal(), voList);
    }

    @Override
    public void clearBrowse(String userId) {
        UserBrowseHistoryQuery query = new UserBrowseHistoryQuery();
        query.setUserId(userId);
        userBrowseHistoryMapper.deleteByParam(query);
    }

    @Override
    public void removeBrowse(String userId, Long historyId) {
        UserBrowseHistory history = userBrowseHistoryMapper.selectByHistoryId(historyId);
        if (history == null || !history.getUserId().equals(userId)) {
            throw new BusinessException("记录不存在");
        }
        userBrowseHistoryMapper.deleteByHistoryId(historyId);
    }

    private String resolveCover(String cover) {
        if (StringTools.isEmpty(cover)) {
            return cover;
        }
        if (cover.contains(",")) {
            return cover.split(",")[0];
        }
        return cover;
    }
}
