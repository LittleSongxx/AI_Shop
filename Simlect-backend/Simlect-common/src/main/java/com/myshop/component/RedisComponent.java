package com.myshop.component;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import com.myshop.constants.Constants;
import com.myshop.entity.config.AppConfig;
import com.myshop.entity.dto.*;
import com.myshop.exception.BusinessException;
import com.myshop.support.PayOrderLifecycleLockHolder;
import com.myshop.redis.RedisUtils;
import com.myshop.utils.DateUtil;
import com.myshop.utils.StringTools;
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

    public String saveToken4Admin(@NotEmpty String account) {
        String accountKey = Constants.REDIS_KEY_TOKEN_ADMIN_ACCOUNT + account;
        Object oldToken = redisUtils.get(accountKey);
        if (oldToken != null && !StringTools.isEmpty(String.valueOf(oldToken))) {
            redisTemplate.delete(Constants.REDIS_KEY_TOKEN_ADMIN + oldToken);
        }
        String token = UUID.randomUUID().toString();
        redisUtils.setex(Constants.REDIS_KEY_TOKEN_ADMIN + token, account, Constants.REDIS_KEY_EXPIRES_DAY);
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
        Object account = redisTemplate.opsForValue().get(Constants.REDIS_KEY_TOKEN_ADMIN + token);
        if (account != null && !StringTools.isEmpty(String.valueOf(account))) {
            redisUtils.delete(Constants.REDIS_KEY_TOKEN_ADMIN_ACCOUNT + account);
        }
        redisTemplate.delete(Constants.REDIS_KEY_TOKEN_ADMIN + token);
    }

    public Object getLoginInfo4Admin(String token) {
        return redisTemplate.opsForValue().get(Constants.REDIS_KEY_TOKEN_ADMIN + token);
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

    public boolean hasSignHashSnapshot(String userId) {
        if (StringTools.isEmpty(userId)) {
            return false;
        }
        String hashKey = Constants.REDIS_KEY_SIGN + userId;
        return stringRedisTemplate.opsForHash().get(hashKey, Constants.TOTAL_SIGN_DAYS) != null;
    }

    public void writeSignHash(String userId, Integer continuousDays, Integer totalSignDays, Integer usedCount) {
        if (StringTools.isEmpty(userId)) {
            return;
        }
        String hashKey = Constants.REDIS_KEY_SIGN + userId;
        stringRedisTemplate.opsForHash().put(hashKey, Constants.CONTINUOUS_DAYS,
                String.valueOf(continuousDays == null ? 0 : continuousDays));
        stringRedisTemplate.opsForHash().put(hashKey, Constants.TOTAL_SIGN_DAYS,
                String.valueOf(totalSignDays == null ? 0 : totalSignDays));
        stringRedisTemplate.opsForHash().put(hashKey, Constants.USED_COUNT,
                String.valueOf(usedCount == null ? 0 : usedCount));
    }

    public String signNullCacheKey(String userId) {
        return Constants.REDIS_KEY_SIGN_NULL + userId;
    }

    public String signRebuildLockKey(String userId) {
        return Constants.REDIS_KEY_SIGN_REBUILD_LOCK + userId;
    }

    public boolean hasSignNullCache(String userId) {
        if (StringTools.isEmpty(userId)) {
            return false;
        }
        String val = stringRedisTemplate.opsForValue().get(signNullCacheKey(userId));
        return Constants.REDIS_SIGN_NULL_PLACEHOLDER.equals(val);
    }

    public void setSignNullCache(String userId) {
        if (StringTools.isEmpty(userId)) {
            return;
        }
        stringRedisTemplate.opsForValue().set(
                signNullCacheKey(userId),
                Constants.REDIS_SIGN_NULL_PLACEHOLDER,
                Constants.SIGN_NULL_CACHE_TTL_SECONDS,
                TimeUnit.SECONDS);
    }

    public void clearSignNullCache(String userId) {
        if (StringTools.isEmpty(userId)) {
            return;
        }
        stringRedisTemplate.delete(signNullCacheKey(userId));
    }

    public void deleteKey(String key) {
        if (StringTools.isEmpty(key)) {
            return;
        }
        stringRedisTemplate.delete(key);
    }

    public String getSignMonthBitmapKey(String userId, String yyyyMM) {
        return Constants.REDIS_KEY_SIGN_MONTH + yyyyMM + ":" + Constants.REDIS_KEY_SIGN_USERID + userId;
    }

    public void setSignBitmapBit(String userId, String yyyyMMdd) {
        if (StringTools.isEmpty(userId) || StringTools.isEmpty(yyyyMMdd) || yyyyMMdd.length() != 8) {
            return;
        }
        String yyyyMM = yyyyMMdd.substring(0, 6);
        int dayOfMonth = Integer.parseInt(yyyyMMdd.substring(6, 8));
        String bitmapKey = getSignMonthBitmapKey(userId, yyyyMM);
        stringRedisTemplate.opsForValue().setBit(bitmapKey, dayOfMonth - 1, true);
    }

    public boolean ensureSignBitmapBit(String userId, String yyyyMMdd) {
        if (StringTools.isEmpty(userId) || StringTools.isEmpty(yyyyMMdd) || yyyyMMdd.length() != 8) {
            return false;
        }
        String yyyyMM = yyyyMMdd.substring(0, 6);
        int dayOfMonth = Integer.parseInt(yyyyMMdd.substring(6, 8));
        String bitmapKey = getSignMonthBitmapKey(userId, yyyyMM);
        int offset = dayOfMonth - 1;
        if (Boolean.TRUE.equals(stringRedisTemplate.opsForValue().getBit(bitmapKey, offset))) {
            return false;
        }
        stringRedisTemplate.opsForValue().setBit(bitmapKey, offset, true);
        return true;
    }

    public boolean initTodaySignBitmapIfAbsent(String userId, String yyyyMM, int dayOfMonth) {
        String bitmapKey = getSignMonthBitmapKey(userId, yyyyMM);
        int offset = dayOfMonth - 1;
        if (Boolean.TRUE.equals(stringRedisTemplate.hasKey(bitmapKey))) {
            if (Boolean.TRUE.equals(stringRedisTemplate.opsForValue().getBit(bitmapKey, offset))) {
                return false;
            }
            return false;
        }
        stringRedisTemplate.opsForValue().setBit(bitmapKey, offset, false);
        return true;
    }

    // 获取已连续签到天数
    public Integer getContinuousDays(String userId) {
        LocalDate today = LocalDate.now();
        LocalDate yesterday = today.minusDays(1);

        // 昨天所在月份
        String yesterdayYyyyMM = yesterday.format(DateTimeFormatter.ofPattern("yyyyMM"));
        String yesterdayKey = Constants.REDIS_KEY_SIGN_MONTH + yesterdayYyyyMM + ":"
                + Constants.REDIS_KEY_SIGN_USERID + userId;

        int yesterdayOffset = yesterday.getDayOfMonth() - 1;

        // 昨天没签到
        if (!redisUtils.bitMapGet(yesterdayKey, yesterdayOffset)) {
            // 检查今天是否签到
            String todayYyyyMM = today.format(DateTimeFormatter.ofPattern("yyyyMM"));
            String todayKey = Constants.REDIS_KEY_SIGN_MONTH + todayYyyyMM + ":"
                    + Constants.REDIS_KEY_SIGN_USERID + userId;
            int todayOffset = today.getDayOfMonth() - 1;

            return redisUtils.bitMapGet(todayKey, todayOffset) ? 1 : 0;
        }

        // 昨天签到了，返回 Hash 里的连续天数
        Integer days = redisUtils.hashGet(Constants.REDIS_KEY_SIGN + userId, Constants.CONTINUOUS_DAYS);
        return days == null ? 0 : days;
    }

    // 获取剩余补签次数（累计签到30天获得1补签次数）
    public Integer getRemainSignCount(String userId) {
        String hashKey = Constants.REDIS_KEY_SIGN + userId;

        List<Object> values = stringRedisTemplate.opsForHash().multiGet(hashKey,
                Arrays.asList(Constants.TOTAL_SIGN_DAYS, Constants.USED_COUNT));

        // StringRedisTemplate 返回的是 String，先转 String 再 parseInt
        String totalStr = values.get(0) != null ? (String) values.get(0) : "0";
        String usedStr = values.get(1) != null ? (String) values.get(1) : "0";

        int totalDays = Integer.parseInt(totalStr);
        int usedCount = Integer.parseInt(usedStr);

        int remain = (totalDays / Constants.LENGTH_30) - usedCount;
        return Math.max(0, remain);
    }

    // 获取累计签到天数
    public Integer totalSignDays(String userId) {
        return redisUtils.hashGet(Constants.REDIS_KEY_SIGN + userId, Constants.TOTAL_SIGN_DAYS) == null ?
                0 :
                redisUtils.hashGet(Constants.REDIS_KEY_SIGN + userId, Constants.TOTAL_SIGN_DAYS);
    }

    // 获取已使用补签次数
    public Integer getUsedCount(String userId) {
        String hashKey = Constants.REDIS_KEY_SIGN + userId;
        String usedStr = stringRedisTemplate.opsForHash().get(hashKey, Constants.USED_COUNT) != null ?
                (String) stringRedisTemplate.opsForHash().get(hashKey, Constants.USED_COUNT) : "0";
        return Integer.parseInt(usedStr);
    }

    // 签到bitMap
    private static final String SIGN_LUA =

            "local todayKey = KEYS[1]; " +
                    "local yesterdayKey = KEYS[2]; " +
                    "local hashKey = KEYS[3]; " +
                    "local todayOffset = tonumber(ARGV[1]); " +
                    "local yesterdayOffset = tonumber(ARGV[2]); " +
                    "if redis.call('getbit', todayKey, todayOffset) == 1 then return {-1, 0, 0} end; " +
                    "redis.call('setbit', todayKey, todayOffset, 1); " +
                    "local yesterdaySigned = redis.call('getbit', yesterdayKey, yesterdayOffset); " +
                    "local continuousDays = 1; " +
                    "if yesterdaySigned == 1 then " +
                    "    local current = redis.call('hget', hashKey, 'continuousDays'); " +
                    "    if current then continuousDays = tonumber(current) + 1 end; " +
                    "end; " +
                    "redis.call('hset', hashKey, 'continuousDays', continuousDays); " +
                    "local totalDays = redis.call('hincrby', hashKey, 'totalSignDays', 1); " +
                    "redis.call('del', ARGV[3]); " +
                    "return {1, continuousDays, totalDays};";

    public void sign(String userId) {
        LocalDate now = LocalDate.now();
        LocalDate yesterday = now.minusDays(1);

        String todayYyyyMM = DateUtil.getTimeOnParttern(0, "yyyyMM");
        String yesterdayYyyyMM = now.getDayOfMonth() == 1
                ? DateUtil.getTimeOnParttern(1, "yyyyMM")
                : todayYyyyMM;

        String todayKey = Constants.REDIS_KEY_SIGN_MONTH + todayYyyyMM + ":"
                + Constants.REDIS_KEY_SIGN_USERID + userId;
        String yesterdayKey = Constants.REDIS_KEY_SIGN_MONTH + yesterdayYyyyMM + ":"
                + Constants.REDIS_KEY_SIGN_USERID + userId;
        String hashKey = Constants.REDIS_KEY_SIGN + userId;

        DefaultRedisScript<List> script = new DefaultRedisScript<>();
        script.setScriptText(SIGN_LUA);
        script.setResultType(List.class);

        List<Long> result = stringRedisTemplate.execute(script,
                Arrays.asList(todayKey, yesterdayKey, hashKey),
                String.valueOf(now.getDayOfMonth() - 1),
                String.valueOf(yesterday.getDayOfMonth() - 1),
                signNullCacheKey(userId)
        );

        if (result.get(0) == -1) {
            throw new BusinessException("今日已签到");
        }
        // result[1]=continuousDays, result[2]=totalDays
    }

    // 判断该天是否签到
    public Boolean isSign(String userId, String yyyyMM, int dayOfMonth) {
        return redisUtils.bitMapGet(Constants.REDIS_KEY_SIGN_MONTH +
                yyyyMM + ":" +
                Constants.REDIS_KEY_SIGN_USERID + userId, dayOfMonth - 1);
    }

    private static final String SUPPLEMENT_LUA =

            "local bitmapKey = KEYS[1]; " +
                    "local hashKey = KEYS[2]; " +
                    "local targetOffset = tonumber(ARGV[1]); " +
                    "if redis.call('getbit', bitmapKey, targetOffset) == 1 then " +
                    "    return -1; " +
                    "end; " +
                    "local totalDays = tonumber(redis.call('hget', hashKey, 'totalSignDays') or '0'); " +
                    "local usedCount = tonumber(redis.call('hget', hashKey, 'usedCount') or '0'); " +
                    "if usedCount >= math.floor(totalDays / 30) then " +
                    "    return -2; " +
                    "end; " +
                    "redis.call('setbit', bitmapKey, targetOffset, 1); " +
                    "redis.call('hincrby', hashKey, 'usedCount', 1); " +
                    "redis.call('hincrby', hashKey, 'totalSignDays', 1); " +
                    "redis.call('del', ARGV[2]); " +
                    "return 1;";

    public void supplementSign(String userId, String yyyyMM, int dayOfMonth) {
        // 1. Lua 原子补签
        String bitmapKey = Constants.REDIS_KEY_SIGN_MONTH + yyyyMM + ":"
                + Constants.REDIS_KEY_SIGN_USERID + userId;
        String hashKey = Constants.REDIS_KEY_SIGN + userId;

        DefaultRedisScript<Long> script = new DefaultRedisScript<>();
        script.setScriptText(SUPPLEMENT_LUA);
        script.setResultType(Long.class);

        Long result = stringRedisTemplate.execute(script,
                Arrays.asList(bitmapKey, hashKey),
                String.valueOf(dayOfMonth - 1),
                signNullCacheKey(userId)
        );

        if (result == -1) throw new  BusinessException("该日期已签到");
        if (result == -2) throw new  BusinessException("补签次数不足");

        // 2. 补签成功后，重新计算连续天数
        int continuousDays = calculateContinuousDays(userId, LocalDate.now());

        // 3. 更新连续天数（这里可能有并发问题，但连续天数不是关键业务数据，可接受）
        stringRedisTemplate.opsForHash().put(hashKey, Constants.CONTINUOUS_DAYS, String.valueOf(continuousDays));
    }

    private int calculateContinuousDays(String userId, LocalDate fromDate) {
        int continuousDays = 0;
        LocalDate date = fromDate;

        // 确定起点：今天签到了就从今天开始，否则从昨天开始
        String todayKey = getBitmapKey(userId, date);
        int todayOffset = date.getDayOfMonth() - 1;
        boolean todaySigned = stringRedisTemplate.opsForValue().getBit(todayKey, todayOffset);

        if (!todaySigned) {
            date = date.minusDays(1);
        }

        // 往前遍历
        while (true) {
            String key = getBitmapKey(userId, date);
            int offset = date.getDayOfMonth() - 1;

            if (stringRedisTemplate.opsForValue().getBit(key, offset)) {
                continuousDays++;
                date = date.minusDays(1);
            } else {
                break;
            }
        }

        return continuousDays;
    }

    private String getBitmapKey(String userId, LocalDate date) {
        return Constants.REDIS_KEY_SIGN_MONTH
                + date.format(DateTimeFormatter.ofPattern("yyyyMM")) + ":"
                + Constants.REDIS_KEY_SIGN_USERID + userId;
    }

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

    public void saveCouponRushingStock(String couponId, Integer stock){
        redisUtils.set(Constants.REDIS_KEY_RUSHING_STOCK + couponId, stock);
    }

    public void markPayTradeInitiated(String payOrderId) {
        if (StringTools.isEmpty(payOrderId)) {
            return;
        }
        redisUtils.setex(
                Constants.REDIS_KEY_PAY_TRADE_INITIATED + payOrderId,
                "1",
                Constants.REDIS_KEY_EXPIRES_DAY);
    }

    public boolean isPayTradeInitiated(String payOrderId) {
        if (StringTools.isEmpty(payOrderId)) {
            return false;
        }
        return redisUtils.get(Constants.REDIS_KEY_PAY_TRADE_INITIATED + payOrderId) != null;
    }

    public boolean tryMarkPayOrderCloseOnce(String payOrderId) {
        if (StringTools.isEmpty(payOrderId)) {
            return false;
        }
        return setIfAbsent(
                Constants.REDIS_KEY_PAY_ORDER_CLOSE_DONE + payOrderId,
                "1",
                Constants.REDIS_KEY_EXPIRES_DAY,
                TimeUnit.SECONDS);
    }

    public boolean isPayOrderCloseMarked(String payOrderId) {
        if (StringTools.isEmpty(payOrderId)) {
            return false;
        }
        return Boolean.TRUE.equals(stringRedisTemplate.hasKey(
                Constants.REDIS_KEY_PAY_ORDER_CLOSE_DONE + payOrderId));
    }

    private static final String PAY_ORDER_LIFECYCLE_UNLOCK_LUA =
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    + "return redis.call('del', KEYS[1]) "
                    + "else return 0 end";

    public void runWithPayOrderLifecycleLock(String payOrderId, Runnable action) {
        runWithPayOrderLifecycleLock(payOrderId, () -> {
            action.run();
            return null;
        });
    }

    public <T> T runWithPayOrderLifecycleLock(String payOrderId, java.util.concurrent.Callable<T> action) {
        if (StringTools.isEmpty(payOrderId)) {
            try {
                return action.call();
            } catch (RuntimeException e) {
                throw e;
            } catch (Exception e) {
                throw new com.myshop.exception.BusinessException("支付订单处理失败", e);
            }
        }
        String lockKey = Constants.REDIS_KEY_PAY_ORDER_LIFECYCLE_LOCK + payOrderId;
        long deadline = System.currentTimeMillis() + Constants.PAY_ORDER_LIFECYCLE_LOCK_WAIT_MS;
        String token = null;
        boolean acquired = false;
        try {
            while (System.currentTimeMillis() < deadline) {
                token = newPayOrderLifecycleLockToken();
                if (setIfAbsent(lockKey, token, Constants.PAY_ORDER_LIFECYCLE_LOCK_SECONDS, TimeUnit.SECONDS)) {
                    acquired = true;
                    PayOrderLifecycleLockHolder.bind(lockKey, token);
                    break;
                }
                Thread.sleep(50L);
            }
            if (!acquired) {
                throw new com.myshop.exception.PayOrderLifecycleBusyException();
            }
            return action.call();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new com.myshop.exception.BusinessException("支付订单处理被中断");
        } catch (RuntimeException e) {
            throw e;
        } catch (Exception e) {
            throw new com.myshop.exception.BusinessException("支付订单处理失败", e);
        } finally {
            if (acquired && token != null) {
                releasePayOrderLifecycleLock(lockKey, token);
            }
            PayOrderLifecycleLockHolder.clear();
        }
    }

    public String getCurrentPayOrderLifecycleLockToken() {
        return PayOrderLifecycleLockHolder.getToken();
    }

    private static String newPayOrderLifecycleLockToken() {
        return UUID.randomUUID() + ":" + Thread.currentThread().getId();
    }

    private void releasePayOrderLifecycleLock(String lockKey, String token) {
        DefaultRedisScript<Long> script = new DefaultRedisScript<>();
        script.setScriptText(PAY_ORDER_LIFECYCLE_UNLOCK_LUA);
        script.setResultType(Long.class);
        Long deleted = stringRedisTemplate.execute(script, List.of(lockKey), token);
        if (deleted == null || deleted == 0L) {
            log.warn("支付生命周期锁释放跳过（非持有者或已过期） lockKey={}", lockKey);
        }
    }

    public boolean tryMarkLatePaymentRefundOnce(String payOrderId) {
        if (StringTools.isEmpty(payOrderId)) {
            return false;
        }
        return setIfAbsent(
                Constants.REDIS_KEY_PAY_LATE_REFUND_DONE + payOrderId,
                "1",
                Constants.REDIS_KEY_EXPIRES_DAY,
                TimeUnit.SECONDS);
    }

    public void clearLatePaymentRefundMark(String payOrderId) {
        if (StringTools.isEmpty(payOrderId)) {
            return;
        }
        stringRedisTemplate.delete(Constants.REDIS_KEY_PAY_LATE_REFUND_DONE + payOrderId);
    }

    // 抢购相关操作：查询是否有购买资格、扣减库存、记录用户预占信息
    // lua
    private static final String RUSHING_LUA =
            "local couponId = ARGV[1]; " +
                    "local userId = ARGV[2]; " +
                    "local userCouponId = ARGV[3]; " +
                    "local stockKey = 'mall:rushing:stock:' .. couponId; " +
                    "local couponKey = 'mall:rushing:coupon:' .. couponId; " +
                    "local userCouponKey = 'mall:rushing:userId:' .. userId .. ':coupon:' .. couponId; " +
                    "local stock = redis.call('get', stockKey); " + // 查库存
                    "if not stock then return 1 end; " +
                    "local stockNum = tonumber(stock); " +
                    "if stockNum ~= -1 and stockNum <= 0 then return 1 end; " +
                    "if redis.call('sismember', couponKey, userId) == 1 then " +
                    "  if redis.call('exists', userCouponKey) == 1 then return 2 end; " + // 有效预占才防重复
                    "  redis.call('srem', couponKey, userId); " + // 预占 hash 已过期则清理脏 SET 成员
                    "end; " +
                    "if stockNum ~= -1 then redis.call('decr', stockKey); end; " + // 限量券扣库存
                    "redis.call('sadd', couponKey, userId); " + // 记录用户
                    "redis.call('hset', userCouponKey, 'couponId', couponId); " +
                    "redis.call('hset', userCouponKey, 'userCouponId', userCouponId); " +
                    "redis.call('hset', userCouponKey, 'time', redis.call('time')[1]); " +
                    "redis.call('expire', userCouponKey, %d); " +
                    "return 0;";

    private static final String RUSH_ROLLBACK_REDIS_ONLY_LUA =
            "local couponId = ARGV[1]; " +
                    "local userId = ARGV[2]; " +
                    "local stockKey = 'mall:rushing:stock:' .. couponId; " +
                    "local couponKey = 'mall:rushing:coupon:' .. couponId; " +
                    "local userCouponKey = 'mall:rushing:userId:' .. userId .. ':coupon:' .. couponId; " +
                    "local legacyKey = 'mall:rushing:userId:' .. userId .. ':order:' .. couponId; " +
                    "local stock = redis.call('get', stockKey); " +
                    "if stock and tonumber(stock) ~= -1 then redis.call('incr', stockKey); end; " +
                    "redis.call('srem', couponKey, userId); " +
                    "redis.call('del', userCouponKey); " +
                    "redis.call('del', legacyKey); " +
                    "return 0;";

    private static final String RUSH_RELEASE_ALIGN_LUA =
            "local couponId = ARGV[1]; " +
                    "local userId = ARGV[2]; " +
                    "local dbRemain = tonumber(ARGV[3]); " +
                    "local stockKey = 'mall:rushing:stock:' .. couponId; " +
                    "local couponKey = 'mall:rushing:coupon:' .. couponId; " +
                    "local userCouponKey = 'mall:rushing:userId:' .. userId .. ':coupon:' .. couponId; " +
                    "local legacyKey = 'mall:rushing:userId:' .. userId .. ':order:' .. couponId; " +
                    "redis.call('set', stockKey, dbRemain); " +
                    "redis.call('srem', couponKey, userId); " +
                    "redis.call('del', userCouponKey); " +
                    "redis.call('del', legacyKey); " +
                    "return 0;";

    public void rollbackRushRedisReserve(String couponId, String userId) {
        if (StringTools.isEmpty(couponId) || StringTools.isEmpty(userId)) {
            return;
        }
        DefaultRedisScript<Long> script = new DefaultRedisScript<>();
        script.setScriptText(RUSH_ROLLBACK_REDIS_ONLY_LUA);
        script.setResultType(Long.class);
        stringRedisTemplate.execute(script, Collections.emptyList(), couponId, userId);
    }

    public void alignRushStockAfterRelease(String couponId, String userId, int dbRemain) {
        if (StringTools.isEmpty(couponId) || StringTools.isEmpty(userId)) {
            return;
        }
        int stock = dbRemain == Constants.RUSHING_STOCK_UNLIMITED
                ? Constants.RUSHING_STOCK_UNLIMITED
                : Math.max(0, dbRemain);
        DefaultRedisScript<Long> script = new DefaultRedisScript<>();
        script.setScriptText(RUSH_RELEASE_ALIGN_LUA);
        script.setResultType(Long.class);
        stringRedisTemplate.execute(script, Collections.emptyList(), couponId, userId, String.valueOf(stock));
    }

    public String getRushUserCouponId(String userId, String couponId) {
        if (StringTools.isEmpty(userId) || StringTools.isEmpty(couponId)) {
            return null;
        }
        String userCouponKey = Constants.REDIS_KEY_RUSHING_USERID + userId + ":coupon:" + couponId;
        Object val = stringRedisTemplate.opsForHash().get(userCouponKey, "userCouponId");
        return val == null ? null : val.toString();
    }

    public boolean hasRushPrepare(String userId, String couponId) {
        if (StringTools.isEmpty(userId) || StringTools.isEmpty(couponId)) {
            return false;
        }
        String userCouponKey = Constants.REDIS_KEY_RUSHING_USERID + userId + ":coupon:" + couponId;
        return Boolean.TRUE.equals(stringRedisTemplate.hasKey(userCouponKey));
    }

    public boolean isUserRushCouponParticipant(String userId, String couponId) {
        if (StringTools.isEmpty(userId) || StringTools.isEmpty(couponId)) {
            return false;
        }
        String couponKey = Constants.REDIS_KEY_RUSHING_COUPON + couponId;
        if (!Boolean.TRUE.equals(stringRedisTemplate.opsForSet().isMember(couponKey, userId))) {
            return false;
        }
        return hasRushPrepare(userId, couponId);
    }

    public Integer rushingCoupon(String couponId, String userId, String userCouponId) {
        // lua原子执行
        DefaultRedisScript<Long> script = new DefaultRedisScript<>();
        script.setScriptText(String.format(RUSHING_LUA, appConfig.getRushPrepareExpireSecond()));
        script.setResultType(Long.class);
        Long result = stringRedisTemplate.execute(script,
                Collections.emptyList(),
                couponId.toString(),
                userId.toString(),
                userCouponId.toString()
        );
        int r = result.intValue();
        return r;
    }

    public void addRagFailRecord(RagDataDTO dto) {
        redisUtils.zsetAdd(Constants.REDIS_RAG_FAIL_RECORD, dto, System.currentTimeMillis());
    }

    public java.util.List<com.myshop.entity.vo.RagSyncFailureVO> listRagFailRedisSnapshots(int offset, int limit) {
        java.util.Set<org.springframework.data.redis.core.ZSetOperations.TypedTuple<Object>> tuples =
                redisTemplate.opsForZSet().reverseRangeWithScores(Constants.REDIS_RAG_FAIL_RECORD, offset, offset + limit - 1L);
        java.util.List<com.myshop.entity.vo.RagSyncFailureVO> list = new java.util.ArrayList<>();
        if (tuples == null) {
            return list;
        }
        for (org.springframework.data.redis.core.ZSetOperations.TypedTuple<Object> tuple : tuples) {
            Object val = tuple.getValue();
            if (!(val instanceof RagDataDTO dto)) {
                continue;
            }
            com.myshop.entity.vo.RagSyncFailureVO vo = new com.myshop.entity.vo.RagSyncFailureVO();
            vo.setDataId(dto.getDataId());
            vo.setDataType(dto.getType());
            vo.setSource("REDIS_DLQ");
            vo.setQueueName(com.myshop.constants.RabbitMQConfig.RAG_DEAD_QUEUE);
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
            JSONObject obj = JSON.parseObject(productSnapshotJson);
            if (obj == null) {
                obj = new JSONObject();
            }
            obj.put("userId", userId);
            return obj.toJSONString();
        } catch (Exception e) {
            JSONObject obj = new JSONObject();
            obj.put("userId", userId);
            obj.put("raw", productSnapshotJson);
            return obj.toJSONString();
        }
    }

    private static boolean isAgentConsultPayloadOwnedByUser(String userId, String json) {
        try {
            JSONObject obj = JSON.parseObject(json);
            if (obj == null) {
                return false;
            }
            return userId.equals(obj.getString("userId"));
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
