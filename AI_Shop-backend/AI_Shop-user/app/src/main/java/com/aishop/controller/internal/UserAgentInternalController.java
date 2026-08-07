package com.aishop.controller.internal;

import com.aishop.biz.ImageModerationService;
import com.aishop.controller.ABaseController;
import com.aishop.entity.po.UserBrowseHistory;
import com.aishop.entity.query.SimplePage;
import com.aishop.entity.query.UserBrowseHistoryQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.mappers.UserBrowseHistoryMapper;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/internal/user/agent")
public class UserAgentInternalController extends ABaseController {

    @Resource
    private UserBrowseHistoryMapper<UserBrowseHistory, UserBrowseHistoryQuery> userBrowseHistoryMapper;

    @Resource
    private ImageModerationService imageModerationService;

    @PostMapping("/verifyImage")
    public ResponseVO<Map<String, Object>> verifyImage(@RequestBody Map<String, Object> body) {
        String userId = body == null || body.get("userId") == null
                ? null : String.valueOf(body.get("userId")).trim();
        String imagePath = body == null || body.get("imagePath") == null
                ? null : String.valueOf(body.get("imagePath")).trim();
        Integer moderationId = null;
        if (body != null && body.get("moderationId") != null) {
            try {
                moderationId = Integer.valueOf(String.valueOf(body.get("moderationId")));
            } catch (NumberFormatException ignored) {
                moderationId = null;
            }
        }
        return getSuccessResponseVO(
                imageModerationService.verifySupportImage(userId, moderationId, imagePath));
    }

    @PostMapping("/latestBrowseProductId")
    public ResponseVO<Map<String, String>> latestBrowseProductId(@RequestBody Map<String, Object> body) {
        String userId = body == null || body.get("userId") == null ? null : String.valueOf(body.get("userId"));
        if (StringTools.isEmpty(userId)) {
            return getSuccessResponseVO(null);
        }
        UserBrowseHistoryQuery q = new UserBrowseHistoryQuery();
        q.setUserId(userId);
        q.setOrderBy(com.aishop.entity.query.SafeSort.of("u.browse_time desc"));
        q.setSimplePage(new SimplePage(0, 1));
        List<UserBrowseHistory> list = userBrowseHistoryMapper.selectList(q);
        if (list == null || list.isEmpty() || StringTools.isEmpty(list.get(0).getProductId())) {
            return getSuccessResponseVO(null);
        }
        Map<String, String> data = new HashMap<>();
        data.put("productId", list.get(0).getProductId());
        return getSuccessResponseVO(data);
    }

    /**
     * Distinct recently-browsed product IDs, newest first.
     *
     * <p>The Agent votes on the category of these products to pick a
     * recommendation shelf, so duplicates are collapsed here: five views of the
     * same phone should count once, not carry the whole vote. Rows are
     * over-fetched because de-duplication happens after the query.
     */
    @PostMapping("/browseHistoryIds")
    public ResponseVO<List<String>> browseHistoryIds(@RequestBody Map<String, Object> body) {
        String userId = body == null || body.get("userId") == null ? null : String.valueOf(body.get("userId"));
        if (StringTools.isEmpty(userId)) {
            return getSuccessResponseVO(Collections.emptyList());
        }
        int limit = 5;
        Object rawLimit = body.get("limit");
        if (rawLimit != null) {
            try {
                limit = Integer.parseInt(String.valueOf(rawLimit));
            } catch (NumberFormatException ignored) {
                limit = 5;
            }
        }
        limit = Math.max(1, Math.min(limit, 20));

        UserBrowseHistoryQuery q = new UserBrowseHistoryQuery();
        q.setUserId(userId);
        q.setOrderBy(com.aishop.entity.query.SafeSort.of("u.browse_time desc"));
        q.setSimplePage(new SimplePage(0, Math.min(limit * 4, 80)));
        List<UserBrowseHistory> list = userBrowseHistoryMapper.selectList(q);
        if (list == null || list.isEmpty()) {
            return getSuccessResponseVO(Collections.emptyList());
        }
        List<String> ids = new ArrayList<>();
        for (UserBrowseHistory row : list) {
            String productId = row.getProductId();
            if (!StringTools.isEmpty(productId) && !ids.contains(productId)) {
                ids.add(productId);
                if (ids.size() >= limit) {
                    break;
                }
            }
        }
        return getSuccessResponseVO(ids);
    }
}
