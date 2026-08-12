package com.aishop.component;

import com.aishop.constants.Constants;
import com.aishop.entity.config.AppConfig;
import com.aishop.entity.dto.*;
import com.aishop.exception.BusinessException;
import com.aishop.support.PayOrderLifecycleLockHolder;
import com.aishop.redis.RedisUtils;
import com.aishop.utils.DateUtil;
import com.aishop.utils.JsonUtils;
import com.aishop.utils.StringTools;
import com.fasterxml.jackson.databind.node.ObjectNode;
import jakarta.annotation.Resource;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.concurrent.TimeUnit;

@Component("redisComponent")
@Slf4j
public class RedisComponent {

    @Resource
    private RedisTemplate redisTemplate;
    @Resource
    private RedisUtils redisUtils;
    @Resource
    private StringRedisTemplate stringRedisTemplate;
    @Resource
    private AppConfig appConfig;

    public void saveCategory2Redis(List<?> list) {
        redisUtils.set(Constants.REDIS_KEY_CATEGORY_LIST, list);
    }

    public String saveCheckCode(String code){
        // 1.对code生成UUID
        String codeKey = UUID.randomUUID().toString();
        // 2.将code和UUID保存到Redis中，设置5分钟过期时间
        redisUtils.setex(Constants.REDIS_KEY_CHECK_CODE + codeKey, code, Constants.LENGTH_5 * 60);
        // 3.返回UUID
        return codeKey;
    }

    public String getCheckCode(@NotEmpty String checkCodeKey) {
        return (String) redisTemplate.opsForValue().get(Constants.REDIS_KEY_CHECK_CODE + checkCodeKey);
    }

    public String saveToken4Admin(@NotNull AdminPrincipalDTO principal) {
        if (StringTools.isEmpty(principal.getAdminId()) || principal.getSessionVersion() == null) {
            throw new IllegalArgumentException("管理员主体缺少ID或会话版本");
        }
        String accountKey = Constants.REDIS_KEY_TOKEN_ADMIN_ACCOUNT + principal.getAdminId();
        Object oldToken = redisUtils.get(accountKey);
        if (oldToken != null && !StringTools.isEmpty(String.valueOf(oldToken))) {
            redisTemplate.delete(Constants.REDIS_KEY_TOKEN_ADMIN + oldToken);
        }
        String token = UUID.randomUUID().toString().replace("-", "");
        stringRedisTemplate.opsForValue().set(
                Constants.REDIS_KEY_ADMIN_SESSION_VERSION + principal.getAdminId(),
                String.valueOf(principal.getSessionVersion()));
        redisUtils.setex(Constants.REDIS_KEY_TOKEN_ADMIN + token, principal, Constants.REDIS_KEY_EXPIRES_DAY);
        redisUtils.setex(accountKey, token, Constants.REDIS_KEY_EXPIRES_DAY);
        return token;
    }

    public void cleanCheckCode(@NotEmpty String checkCodeKey) {
        redisTemplate.delete(Constants.REDIS_KEY_CHECK_CODE + checkCodeKey);
    }

    public void cleanToken4Admin(String token) {
        if (StringTools.isEmpty(token)) {
            return;
        }
        AdminPrincipalDTO principal = parseAdminPrincipal(
                redisTemplate.opsForValue().get(Constants.REDIS_KEY_TOKEN_ADMIN + token));
        if (principal != null && !StringTools.isEmpty(principal.getAdminId())) {
            Object mappedToken = redisUtils.get(
                    Constants.REDIS_KEY_TOKEN_ADMIN_ACCOUNT + principal.getAdminId());
            if (token.equals(String.valueOf(mappedToken))) {
                redisUtils.delete(Constants.REDIS_KEY_TOKEN_ADMIN_ACCOUNT + principal.getAdminId());
            }
        }
        redisTemplate.delete(Constants.REDIS_KEY_TOKEN_ADMIN + token);
    }

    public AdminPrincipalDTO getAdminPrincipal(String token) {
        if (StringTools.isEmpty(token)) {
            return null;
        }
        AdminPrincipalDTO principal = parseAdminPrincipal(
                redisTemplate.opsForValue().get(Constants.REDIS_KEY_TOKEN_ADMIN + token));
        if (principal == null || StringTools.isEmpty(principal.getAdminId())
                || principal.getSessionVersion() == null) {
            return null;
        }
        String currentVersion = stringRedisTemplate.opsForValue().get(
                Constants.REDIS_KEY_ADMIN_SESSION_VERSION + principal.getAdminId());
        if (!String.valueOf(principal.getSessionVersion()).equals(currentVersion)) {
            redisTemplate.delete(Constants.REDIS_KEY_TOKEN_ADMIN + token);
            return null;
        }
        return principal;
    }

    public Object getLoginInfo4Admin(String token) {
        return getAdminPrincipal(token);
    }

    public void invalidateAdminSessions(String adminId, long sessionVersion) {
        if (StringTools.isEmpty(adminId)) {
            return;
        }
        String accountKey = Constants.REDIS_KEY_TOKEN_ADMIN_ACCOUNT + adminId;
        Object token = redisUtils.get(accountKey);
        if (token != null && !StringTools.isEmpty(String.valueOf(token))) {
            redisTemplate.delete(Constants.REDIS_KEY_TOKEN_ADMIN + token);
        }
        redisUtils.delete(accountKey);
        stringRedisTemplate.opsForValue().set(
                Constants.REDIS_KEY_ADMIN_SESSION_VERSION + adminId,
                String.valueOf(sessionVersion));
    }

    private AdminPrincipalDTO parseAdminPrincipal(Object value) {
        if (value instanceof AdminPrincipalDTO principal) {
            return principal;
        }
        if (!(value instanceof Map<?, ?> map)) {
            // String-only sessions are deliberately rejected after the RBAC migration.
            return null;
        }
        Object adminId = map.get("adminId");
        Object account = map.get("account");
        Object displayName = map.get("displayName");
        Object version = map.get("sessionVersion");
        if (adminId == null || version == null) {
            return null;
        }
        AdminPrincipalDTO principal = new AdminPrincipalDTO();
        principal.setAdminId(String.valueOf(adminId));
        principal.setAccount(account == null ? null : String.valueOf(account));
        principal.setDisplayName(displayName == null ? null : String.valueOf(displayName));
        try {
            principal.setSessionVersion(Long.parseLong(String.valueOf(version)));
        } catch (NumberFormatException e) {
            return null;
        }
        principal.setRoles(toStringSet(map.get("roles")));
        principal.setPermissions(toStringSet(map.get("permissions")));
        return principal;
    }

    private Set<String> toStringSet(Object value) {
        if (!(value instanceof Collection<?> collection)) {
            return Collections.emptySet();
        }
        Set<String> values = new LinkedHashSet<>();
        for (Object item : collection) {
            if (item != null && !StringTools.isEmpty(String.valueOf(item))) {
                values.add(String.valueOf(item));
            }
        }
        return values;
    }

    @SuppressWarnings("unchecked")
    public List<?> getCategoryList() {
        List<?> list = (List<?>) redisUtils.get(Constants.REDIS_KEY_CATEGORY_LIST);
        return list == null ? null : list;
    }

    // 从TokenUserInfoDTO中获取token，并存入redis，清除旧token,返回新token
    public String saveTokenUserInfo(TokenUserInfoDTO tokenUserInfoDTO) {
        // 从TokenUserInfoDTO中获取token
        String oldtoken = tokenUserInfoDTO.getToken();
        // 如果有旧token，则清除旧token
        if (oldtoken != null) {
            cleanTokenUserInfo(oldtoken);
        }
        // 存入redis
        // 生成新token,去除UUID中间的横线
        String token = UUID.randomUUID().toString().replace("-", "");
        tokenUserInfoDTO.setToken(token);
        // 1.userId -> token
        redisUtils.setex(Constants.REDIS_KEY_TOKEN_USERID_WEB + tokenUserInfoDTO.getUserId(), tokenUserInfoDTO, Constants.REDIS_KEY_EXPIRES_DAY);
        // 2.token -> userId
        redisUtils.setex(Constants.REDIS_KEY_TOKEN_WEB + token, tokenUserInfoDTO, Constants.REDIS_KEY_EXPIRES_DAY);
        // 返回新token
        return token;
    }

    // 清除旧的1.userId -> token；2.token -> userId
    public void cleanTokenUserInfo(String token) {
        // 根据旧token获取用户信息对象
        TokenUserInfoDTO userInfo = (TokenUserInfoDTO) redisUtils.get(Constants.REDIS_KEY_TOKEN_WEB + token);
        if (userInfo != null) {
            // 1.清除userId -> TokenUserInfoDTO
            redisUtils.delete(Constants.REDIS_KEY_TOKEN_USERID_WEB + userInfo.getUserId());
            // 2.清除token -> TokenUserInfoDTO
            redisUtils.delete(Constants.REDIS_KEY_TOKEN_WEB + token);
        }
    }

    // 根据token获取TokenUserInfoDTO
    public TokenUserInfoDTO getTokenUserInfo(String token) {
        // 查询TokenUserInfoDTO
        TokenUserInfoDTO tokenUserInfoDTO = (TokenUserInfoDTO) redisUtils.get(Constants.REDIS_KEY_TOKEN_WEB + token);
        // 判断是否存在
        // 若不存在则返回null
        return tokenUserInfoDTO == null ? null : tokenUserInfoDTO;
    }

    // 根据userId获取TokenUserInfoDTO
    public TokenUserInfoDTO getTokenUserInfoByUserId(String userId) {
        TokenUserInfoDTO tokenUserInfoDTO = (TokenUserInfoDTO) redisUtils.get(Constants.REDIS_KEY_TOKEN_USERID_WEB + userId);
        // 存在则返回
        return tokenUserInfoDTO == null ? null : tokenUserInfoDTO;
    }

    public String getUserIdByToken(String token) {
        // 根据token获取用户信息
        TokenUserInfoDTO userInfo = getTokenUserInfo(token);
        // 判断是否存在
        if (userInfo == null || StringTools.isEmpty(userInfo.getUserId())){
            return null;
        }
        // 返回userId
        return userInfo.getUserId();
    }

    // 添加到延时队列
    public void addOrder2DelayQueue(String queueName,Integer delayMinute,String orderId){
        // zset,以当前时间毫秒+delayMinute转毫秒为score
        long expireTime = System.currentTimeMillis() + delayMinute * 60 * 1000;
        redisUtils.zsetAdd(queueName, orderId, expireTime);
    }

    // 获取超时订单
    public Set<String> getTimeOutOrder(String queueName){
        // 从score为0开始到当前时间顺序取出
        return redisUtils.zsetRangeByScore(queueName, 0, System.currentTimeMillis());
    }

    // 移除超时订单
    public long removeTimeOutOrder(String queueName,String orderId){
        return redisUtils.zsetAddRemove(queueName, orderId);
    }

    public void saveLogistics(LogisticsSendDTO logisticsSendDTO) {
        redisUtils.set(Constants.REDIS_KEY_SETTING_LOGISTICS, logisticsSendDTO);
    }

    public LogisticsSendDTO getLogistics(String senderName) {
        return (LogisticsSendDTO) redisUtils.get(Constants.REDIS_KEY_SETTING_LOGISTICS + senderName);
    }

    public void addOrder2DeliverQueue(String redisKeyOrderDelayQueue, Integer delaySecond, String orderId) {
        redisUtils.zsetAdd(redisKeyOrderDelayQueue, orderId, System.currentTimeMillis() + delaySecond * 1000);
    }

    public LogisticsSendDTO getLogisticsInfo() {
        return (LogisticsSendDTO) redisUtils.get(Constants.REDIS_KEY_SETTING_LOGISTICS);
    }

    public void saveSignRewardConfig(SignRewardConfigDTO config) {
        redisUtils.set(Constants.REDIS_KEY_SIGN_REWARD_CONFIG, config);
    }

    public SignRewardConfigDTO getSignRewardConfig() {
        return (SignRewardConfigDTO) redisUtils.get(Constants.REDIS_KEY_SIGN_REWARD_CONFIG);
    }

    public void saveMemberLevelRewardConfig(MemberLevelRewardConfigDTO config) {
        redisUtils.set(Constants.REDIS_KEY_MEMBER_LEVEL_REWARD_CONFIG, config);
    }

    public MemberLevelRewardConfigDTO getMemberLevelRewardConfig() {
        return (MemberLevelRewardConfigDTO) redisUtils.get(Constants.REDIS_KEY_MEMBER_LEVEL_REWARD_CONFIG);
    }

    public void saveUserLocationCoords(String userId, UserLocationCoordsDTO coords) {
        if (StringTools.isEmpty(userId) || coords == null) {
            return;
        }
        redisUtils.setex(Constants.REDIS_KEY_USER_LOCATION + userId, coords, appConfig.getUserLocationExpireDay() * 24L * 3600);
    }

    public UserLocationCoordsDTO getUserLocationCoords(String userId) {
        if (StringTools.isEmpty(userId)) {
            return null;
        }
        return (UserLocationCoordsDTO) redisUtils.get(Constants.REDIS_KEY_USER_LOCATION + userId);
    }

    public void updateUser(String userId) {
        // 修改个人资料后无需重新登录
        // 只需修改token对应的用户信息
        TokenUserInfoDTO tokenUserInfoDTO = getTokenUserInfoByUserId(userId);
        // 获取原token
        String token = tokenUserInfoDTO.getToken();
        // 不改变token，只修改用户信息
        redisUtils.set(Constants.REDIS_KEY_TOKEN_USERID_WEB + userId, tokenUserInfoDTO);
        redisUtils.set(Constants.REDIS_KEY_TOKEN_WEB + token, tokenUserInfoDTO);
    }

    public void cleanAllToken(@NotNull String userId) {
        TokenUserInfoDTO tokenUserInfoDTO = getTokenUserInfoByUserId(userId);
        if (tokenUserInfoDTO != null) {
            cleanTokenUserInfo(tokenUserInfoDTO.getToken());
        }
    }

    private static String cancelAgentMessageRedisKey(String userId, Integer messageId) {
        return Constants.REDIS_KEY_CANCEL_AGENT_MESSAGE + userId + ":msg:" + messageId;
    }

    private static String agentConsultProductRedisKey(String userId) {
        return Constants.REDIS_KEY_AGENT_CONSULT_PRODUCT + userId;
    }

    private static String productConsultActiveKey(String userId) {
        return Constants.REDIS_KEY_AGENT_CONSULT_ACTIVE + userId;
    }

    public void markProductConsultActive(String userId) {
        if (StringTools.isEmpty(userId)) {
            return;
        }
        redisUtils.setex(productConsultActiveKey(userId), "1", 30 * 60);
    }

    public boolean hasProductConsultActive(String userId) {
        if (StringTools.isEmpty(userId)) {
            return false;
        }
        return redisUtils.get(productConsultActiveKey(userId)) != null;
    }

    public void clearProductConsultActive(String userId) {
        if (StringTools.isEmpty(userId)) {
            return;
        }
        redisUtils.delete(productConsultActiveKey(userId));
    }

    // 保存取消消息队列，五分钟过期
    public void saveCancelMessageQueue(String userId, Integer cancelMessageId) {
        if (StringTools.isEmpty(userId) || cancelMessageId == null) {
            return;
        }
        redisUtils.setex(cancelAgentMessageRedisKey(userId, cancelMessageId), cancelMessageId, Constants.LENGTH_5 * 60);
    }

    // 判断当前redis中有无该取消消息
    public Boolean hasCancelMessage(String userId, Integer cancelMessageId) {
        if (StringTools.isEmpty(userId) || cancelMessageId == null) {
            return false;
        }
        return redisUtils.get(cancelAgentMessageRedisKey(userId, cancelMessageId)) != null;
    }

    public String getPrompt(@NotEmpty String key) {
        return (String) redisUtils.get(Constants.REDIS_KEY_PROMPT + key);
    }

    public void savePrompt(@NotEmpty String key, String prompt) {
        redisUtils.set(Constants.REDIS_KEY_PROMPT + key, prompt);
    }

    public void cleanPrompt(@NotEmpty String key) {
        redisUtils.delete(Constants.REDIS_KEY_PROMPT + key);
    }

    // 签到读写整体搬到 SignRedisComponent：签到的 bitmap/hash/空值缓存三种键要一起改，放一起才不会只改一半。

    public void recordBrowseRecent(String userId, String productId) {
        if (StringTools.isEmpty(userId) || StringTools.isEmpty(productId)) {
            return;
        }
        String key = Constants.REDIS_KEY_BROWSE_RECENT + userId;
        double score = System.currentTimeMillis();
        stringRedisTemplate.opsForZSet().add(key, productId, score);
        Long size = stringRedisTemplate.opsForZSet().zCard(key);
        if (size != null && size > Constants.BROWSE_REDIS_MAX_SIZE) {
            stringRedisTemplate.opsForZSet().removeRange(key, 0, size - Constants.BROWSE_REDIS_MAX_SIZE - 1);
        }
        stringRedisTemplate.expire(key, Constants.REDIS_KEY_EXPIRES_DAY, java.util.concurrent.TimeUnit.SECONDS);
    }

    // 支付单生命周期的锁与一次性标记搬到 PayOrderRedisComponent。
    // 抢购预占（库存计数 + 参与者 SET + 预占 hash 的联动 Lua）搬到 CouponRushRedisComponent。

    public void addRagFailRecord(RagDataDTO dto) {
        redisUtils.zsetAdd(Constants.REDIS_RAG_FAIL_RECORD, dto, System.currentTimeMillis());
    }

    public java.util.List<com.aishop.entity.vo.RagSyncFailureVO> listRagFailRedisSnapshots(int offset, int limit) {
        java.util.Set<org.springframework.data.redis.core.ZSetOperations.TypedTuple<Object>> tuples =
                redisTemplate.opsForZSet().reverseRangeWithScores(Constants.REDIS_RAG_FAIL_RECORD, offset, offset + limit - 1L);
        java.util.List<com.aishop.entity.vo.RagSyncFailureVO> list = new java.util.ArrayList<>();
        if (tuples == null) {
            return list;
        }
        for (org.springframework.data.redis.core.ZSetOperations.TypedTuple<Object> tuple : tuples) {
            Object val = tuple.getValue();
            if (!(val instanceof RagDataDTO dto)) {
                continue;
            }
            com.aishop.entity.vo.RagSyncFailureVO vo = new com.aishop.entity.vo.RagSyncFailureVO();
            vo.setDataId(dto.getDataId());
            vo.setDataType(dto.getType());
            vo.setSource("REDIS_DLQ");
            vo.setQueueName(com.aishop.constants.RabbitMQConfig.RAG_DEAD_QUEUE);
            if (tuple.getScore() != null) {
                vo.setCreateTime(new java.util.Date(tuple.getScore().longValue()));
            }
            vo.setErrorMessage("Redis 死信快照（向量同步最终失败）");
            list.add(vo);
        }
        return list;
    }

    public long countRagFailRedisSnapshots() {
        Long size = redisTemplate.opsForZSet().size(Constants.REDIS_RAG_FAIL_RECORD);
        return size == null ? 0L : size;
    }

    public void removeRagFailRedisSnapshot(String dataId, String dataType) {
        if (StringTools.isEmpty(dataId)) {
            return;
        }
        RagDataDTO probe = new RagDataDTO(dataId, dataType);
        redisUtils.zsetAddRemove(Constants.REDIS_RAG_FAIL_RECORD, probe);
    }

    public void saveAgentConsultProduct(String userId, String productSnapshotJson) {
        if (StringTools.isEmpty(userId) || StringTools.isEmpty(productSnapshotJson)) {
            return;
        }
        String payload = ensureAgentConsultPayloadUserId(userId, productSnapshotJson);
        redisUtils.setex(agentConsultProductRedisKey(userId), payload, Constants.REDIS_KEY_EXPIRES_DAY);
    }

    public String getAgentConsultProduct(String userId) {
        if (StringTools.isEmpty(userId)) {
            return null;
        }
        Object raw = redisUtils.get(agentConsultProductRedisKey(userId));
        if (raw == null) {
            return null;
        }
        String json = String.valueOf(raw);
        if (!isAgentConsultPayloadOwnedByUser(userId, json)) {
            redisUtils.delete(agentConsultProductRedisKey(userId));
            return null;
        }
        return json;
    }

    public void clearAgentConsultProduct(String userId) {
        if (StringTools.isEmpty(userId)) {
            return;
        }
        redisUtils.delete(agentConsultProductRedisKey(userId));
    }

    public void bindAgentPendingMessageContext(String userId, Integer messageId) {
        if (StringTools.isEmpty(userId) || messageId == null) {
            return;
        }
        redisUtils.setex(Constants.REDIS_KEY_AGENT_PENDING_MSG_CTX + userId, messageId, Constants.LENGTH_5 * 60);
    }

    public Integer getAgentPendingMessageContext(String userId) {
        if (StringTools.isEmpty(userId)) {
            return null;
        }
        Object raw = redisUtils.get(Constants.REDIS_KEY_AGENT_PENDING_MSG_CTX + userId);
        if (raw == null) {
            return null;
        }
        if (raw instanceof Integer i) {
            return i;
        }
        try {
            return Integer.parseInt(String.valueOf(raw));
        } catch (NumberFormatException e) {
            return null;
        }
    }

    public void clearAgentPendingMessageContext(String userId) {
        if (StringTools.isEmpty(userId)) {
            return;
        }
        redisUtils.delete(Constants.REDIS_KEY_AGENT_PENDING_MSG_CTX + userId);
    }

    public void deleteCacheKey(String key) {
        if (StringTools.isEmpty(key)) {
            return;
        }
        redisUtils.delete(key);
    }

    private static String ensureAgentConsultPayloadUserId(String userId, String productSnapshotJson) {
        try {
            ObjectNode obj = JsonUtils.parseTree(productSnapshotJson) instanceof ObjectNode on
                    ? on.deepCopy()
                    : JsonUtils.createObjectNode();
            obj.put("userId", userId);
            return JsonUtils.toJson(obj);
        } catch (Exception e) {
            ObjectNode obj = JsonUtils.createObjectNode();
            obj.put("userId", userId);
            obj.put("raw", productSnapshotJson);
            return JsonUtils.toJson(obj);
        }
    }

    private static boolean isAgentConsultPayloadOwnedByUser(String userId, String json) {
        try {
            var node = JsonUtils.parseTree(json);
            if (node == null || !node.hasNonNull("userId")) {
                return false;
            }
            return userId.equals(node.get("userId").asText());
        } catch (Exception e) {
            return false;
        }
    }

    public boolean setIfAbsent(String key, String value, long timeout, TimeUnit unit) {
        Boolean ok = stringRedisTemplate.opsForValue().setIfAbsent(key, value, timeout, unit);
        return Boolean.TRUE.equals(ok);
    }

    private static final String MEMBER_LEVEL_CLAIM_PREFIX = "member:level:claim:";

    public java.util.Set<Integer> getMemberLevelClaimed(String userId) {
        String raw = stringRedisTemplate.opsForValue().get(MEMBER_LEVEL_CLAIM_PREFIX + userId);
        java.util.Set<Integer> set = new java.util.HashSet<>();
        if (StringTools.isEmpty(raw)) {
            return set;
        }
        for (String part : raw.split(",")) {
            if (StringTools.isEmpty(part)) {
                continue;
            }
            try {
                set.add(Integer.parseInt(part.trim()));
            } catch (NumberFormatException ignored) {
            }
        }
        return set;
    }

    public void addMemberLevelClaimed(String userId, int levelCode) {
        java.util.Set<Integer> set = getMemberLevelClaimed(userId);
        set.add(levelCode);
        StringBuilder sb = new StringBuilder();
        for (Integer code : set) {
            if (sb.length() > 0) {
                sb.append(',');
            }
            sb.append(code);
        }
        stringRedisTemplate.opsForValue().set(MEMBER_LEVEL_CLAIM_PREFIX + userId, sb.toString());
    }

    public long incr(String key) {
        return stringRedisTemplate.opsForValue().increment(key);
    }

    public long decr(String key) {
        return stringRedisTemplate.opsForValue().decrement(key);
    }

    public long getCounter(String key) {
        String value = stringRedisTemplate.opsForValue().get(key);
        return value == null ? 0 : Long.parseLong(value);
    }

    public void setCounter(String key, long value) {
        stringRedisTemplate.opsForValue().set(key, String.valueOf(value));
    }

    public void deleteCounter(String key) {
        stringRedisTemplate.delete(key);
    }

    // 邮件验证码
    public void saveEmailCode(String email, String code){
        // 将email和code保存到Redis中，设置5分钟过期时间
        redisUtils.setex(Constants.REDIS_KEY_EMAIL_CODE + email, code, Constants.LENGTH_5 * 60);
    }

    // 获取邮件验证码
    public String getEmailCode(@NotEmpty String email) {
        return (String) redisTemplate.opsForValue().get(Constants.REDIS_KEY_EMAIL_CODE + email);
    }

    // 清理邮件验证码
    public void cleanEmailCode(@NotEmpty String email) {
        redisTemplate.delete(Constants.REDIS_KEY_EMAIL_CODE + email);
    }
}
