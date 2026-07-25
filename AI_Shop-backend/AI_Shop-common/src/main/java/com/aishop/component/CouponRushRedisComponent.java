package com.aishop.component;

import com.aishop.constants.Constants;
import com.aishop.entity.config.AppConfig;
import com.aishop.redis.RedisUtils;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

import java.util.Collections;

/**
 * 优惠券抢购的 Redis 预占。
 * <p>从 RedisComponent 拆出来的：预占是"库存计数 + 参与者 SET + 用户预占 hash"三个键的联动，
 * 三者必须在同一段 Lua 里改，任何一处漏改都会漏券或超卖。这类脚本放在通用组件里很难看清全貌。
 * <p>库存以数据库为准，这里的计数只是挡住绝大多数无效请求的前置闸门，扣减仍要回数据库确认。
 */
@Component("couponRushRedisComponent")
@Slf4j
public class CouponRushRedisComponent {

	@Resource
	private RedisUtils redisUtils;

	@Resource
	private StringRedisTemplate stringRedisTemplate;

	@Resource
	private AppConfig appConfig;

	public void saveCouponRushingStock(String couponId, Integer stock) {
		redisUtils.set(Constants.REDIS_KEY_RUSHING_STOCK + couponId, stock);
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
					"redis.call('expire', userCouponKey, tonumber(ARGV[4])); " +
					"return 0;";

	/**
	 * 脚本实例复用：{@link DefaultRedisScript} 内部对 sha1 做了加锁的懒加载，多线程共享一个实例是安全的。
	 * 每次调用新建实例会重算 sha1、退回 EVAL 传全量脚本文本，抢购这种高频路径没必要。
	 * 预占过期时间用 ARGV 传入而不是 String.format，脚本文本恒定才能一直命中同一个 EVALSHA。
	 */
	private static final DefaultRedisScript<Long> RUSHING_SCRIPT = new DefaultRedisScript<>(RUSHING_LUA, Long.class);

	private static final String RUSH_ROLLBACK_REDIS_ONLY_LUA =
			"local couponId = ARGV[1]; " +
					"local userId = ARGV[2]; " +
					"local stockKey = 'mall:rushing:stock:' .. couponId; " +
					"local couponKey = 'mall:rushing:coupon:' .. couponId; " +
					"local userCouponKey = 'mall:rushing:userId:' .. userId .. ':coupon:' .. couponId; " +
					"local stock = redis.call('get', stockKey); " +
					"if stock and tonumber(stock) ~= -1 then redis.call('incr', stockKey); end; " +
					"redis.call('srem', couponKey, userId); " +
					"redis.call('del', userCouponKey); " +
					"return 0;";

	private static final String RUSH_RELEASE_ALIGN_LUA =
			"local couponId = ARGV[1]; " +
					"local userId = ARGV[2]; " +
					"local dbRemain = tonumber(ARGV[3]); " +
					"local stockKey = 'mall:rushing:stock:' .. couponId; " +
					"local couponKey = 'mall:rushing:coupon:' .. couponId; " +
					"local userCouponKey = 'mall:rushing:userId:' .. userId .. ':coupon:' .. couponId; " +
					"redis.call('set', stockKey, dbRemain); " +
					"redis.call('srem', couponKey, userId); " +
					"redis.call('del', userCouponKey); " +
					"return 0;";

	private static final DefaultRedisScript<Long> RUSH_ROLLBACK_REDIS_ONLY_SCRIPT =
			new DefaultRedisScript<>(RUSH_ROLLBACK_REDIS_ONLY_LUA, Long.class);

	private static final DefaultRedisScript<Long> RUSH_RELEASE_ALIGN_SCRIPT =
			new DefaultRedisScript<>(RUSH_RELEASE_ALIGN_LUA, Long.class);

	public void rollbackRushRedisReserve(String couponId, String userId) {
		if (StringTools.isEmpty(couponId) || StringTools.isEmpty(userId)) {
			return;
		}
		stringRedisTemplate.execute(RUSH_ROLLBACK_REDIS_ONLY_SCRIPT, Collections.emptyList(), couponId, userId);
	}

	public void alignRushStockAfterRelease(String couponId, String userId, int dbRemain) {
		if (StringTools.isEmpty(couponId) || StringTools.isEmpty(userId)) {
			return;
		}
		int stock = dbRemain == Constants.RUSHING_STOCK_UNLIMITED
				? Constants.RUSHING_STOCK_UNLIMITED
				: Math.max(0, dbRemain);
		stringRedisTemplate.execute(RUSH_RELEASE_ALIGN_SCRIPT, Collections.emptyList(),
				couponId, userId, String.valueOf(stock));
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

	/**
	 * 只在 SET 里有、预占 hash 已过期的，不算参与者：预占过期就等于资格已释放。
	 */
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

	/**
	 * @return 0=预占成功，1=库存不足，2=重复下单，3=其它异常（返回码含义见调用方 assertRushCode）
	 */
	public Integer rushingCoupon(String couponId, String userId, String userCouponId) {
		// lua原子执行
		Long result = stringRedisTemplate.execute(RUSHING_SCRIPT,
				Collections.emptyList(),
				couponId,
				userId,
				userCouponId,
				String.valueOf(appConfig.getRushPrepareExpireSecond())
		);
		return result.intValue();
	}
}
