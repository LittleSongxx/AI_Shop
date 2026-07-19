package com.simlect.controller.internal;

import com.simlect.controller.ABaseController;
import com.simlect.entity.po.UserBrowseHistory;
import com.simlect.entity.query.SimplePage;
import com.simlect.entity.query.UserBrowseHistoryQuery;
import com.simlect.entity.vo.ResponseVO;
import com.simlect.mappers.UserBrowseHistoryMapper;
import com.simlect.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/internal/user/agent")
public class UserAgentInternalController extends ABaseController {

    @Resource
    private UserBrowseHistoryMapper<UserBrowseHistory, UserBrowseHistoryQuery> userBrowseHistoryMapper;

    @PostMapping("/latestBrowseProductId")
    public ResponseVO<Map<String, String>> latestBrowseProductId(@RequestBody Map<String, Object> body) {
        String userId = body == null || body.get("userId") == null ? null : String.valueOf(body.get("userId"));
        if (StringTools.isEmpty(userId)) {
            return getSuccessResponseVO(null);
        }
        UserBrowseHistoryQuery q = new UserBrowseHistoryQuery();
        q.setUserId(userId);
        q.setOrderBy("u.browse_time desc");
        q.setSimplePage(new SimplePage(0, 1));
        List<UserBrowseHistory> list = userBrowseHistoryMapper.selectList(q);
        if (list == null || list.isEmpty() || StringTools.isEmpty(list.get(0).getProductId())) {
            return getSuccessResponseVO(null);
        }
        Map<String, String> data = new HashMap<>();
        data.put("productId", list.get(0).getProductId());
        return getSuccessResponseVO(data);
    }
}
