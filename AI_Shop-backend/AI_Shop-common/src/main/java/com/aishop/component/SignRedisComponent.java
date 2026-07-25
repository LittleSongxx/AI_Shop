package com.aishop.component;

import com.aishop.constants.Constants;
import com.aishop.exception.BusinessException;
import com.aishop.redis.LuaScriptLoader;
import com.aishop.redis.RedisUtils;
import com.aishop.utils.DateUtil;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * 签到相关的 Redis 读写。
 * <p>从 RedisComponent 拆出来的：签到用了三种结构（月度 bitmap 记哪天签了、hash 存累计/连续/补签次数、
 * 空值缓存挡穿透），它们的键格式和失效关系必须一起改，散在一个 1000 行的通用组件里很容易只改一半。
 * <p>数据库是签到记录的最终来源，这里的 hash 是可重建的缓存，重建逻辑见 SignRecordSyncService。
 */
@Component("signRedisComponent")
@Slf4j
public class SignRedisComponent {

	@Resource
	private RedisUtils redisUtils;

	@Resource
	private StringRedisTemplate stringRedisTemplate;

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

	// 签到 bitMap，脚本见 resources/lua/sign_v1.lua
	// 脚本对象持有惰性计算的 SHA1，复用同一实例才能走 EVALSHA 而不是每次重传脚本全文
	private static final DefaultRedisScript<List> SIGN_SCRIPT =
			LuaScriptLoader.load("sign_v1.lua", List.class);

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

		List<Long> result = stringRedisTemplate.execute(SIGN_SCRIPT,
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

	// 补签，脚本见 resources/lua/sign_supplement_v1.lua
	private static final DefaultRedisScript<Long> SUPPLEMENT_SCRIPT =
			LuaScriptLoader.load("sign_supplement_v1.lua", Long.class);

	public void supplementSign(String userId, String yyyyMM, int dayOfMonth) {
		// 1. Lua 原子补签
		String bitmapKey = Constants.REDIS_KEY_SIGN_MONTH + yyyyMM + ":"
				+ Constants.REDIS_KEY_SIGN_USERID + userId;
		String hashKey = Constants.REDIS_KEY_SIGN + userId;

		Long result = stringRedisTemplate.execute(SUPPLEMENT_SCRIPT,
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
}
