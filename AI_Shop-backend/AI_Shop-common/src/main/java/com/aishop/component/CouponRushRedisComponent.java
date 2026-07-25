package com.aishop.component;

import com.aishop.constants.Constants;
import com.aishop.entity.config.AppConfig;
import com.aishop.redis.LuaScriptLoader;
import com.aishop.redis.RedisUtils;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.Cursor;
import org.springframework.data.redis.core.ScanOptions;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

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

	/**
	 * 抢购预占：查询是否有购买资格、扣减库存、记录用户预占信息。脚本见 {@code resources/lua/coupon_rush_reserve_v1.lua}。
	 * <p>脚本实例复用：{@link DefaultRedisScript} 内部对 sha1 做了加锁的懒加载，多线程共享一个实例是安全的。
	 * 每次调用新建实例会重算 sha1、退回 EVAL 传全量脚本文本，抢购这种高频路径没必要。
	 * 预占过期时间用 ARGV 传入而不是拼进脚本文本，脚本恒定才能一直命中同一个 EVALSHA。
	 */
	private static final DefaultRedisScript<Long> RUSHING_SCRIPT =
			LuaScriptLoader.load("coupon_rush_reserve_v1.lua", Long.class);

	private static final DefaultRedisScript<Long> RUSH_ROLLBACK_REDIS_ONLY_SCRIPT =
			LuaScriptLoader.load("coupon_rush_rollback_v1.lua", Long.class);

	private static final DefaultRedisScript<Long> RUSH_RELEASE_ALIGN_SCRIPT =
			LuaScriptLoader.load("coupon_rush_release_align_v1.lua", Long.class);

	private static final DefaultRedisScript<Long> RUSH_SWEEP_DANGLING_SCRIPT =
			LuaScriptLoader.load("coupon_rush_sweep_dangling_v1.lua", Long.class);

	/**
	 * 参与者 SET 的单批清理批量。一次脚本执行是原子的、会独占 Redis，批越大阻塞越久，
	 * 所以按批切开而不是把整个 SET 丢进去。
	 */
	private static final int SWEEP_BATCH_SIZE = 200;

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
	 * 摘掉参与者 SET 里预占已过期的僵尸成员。脚本见 {@code resources/lua/coupon_rush_sweep_dangling_v1.lua}。
	 * <p>SET 没有 TTL，正常路径靠回滚或用户下次抢购时的懒清理收敛；两者都没发生时成员会永久残留，
	 * SET 只增不减。判定条件与预占脚本里的懒清理一致，所以这里只是提前做掉，不改变业务判定。
	 * <p>用 SSCAN 遍历而不是 SMEMBERS：热门券的 SET 可能很大，一次性取回会打爆网络和内存。
	 *
	 * @return 摘掉的成员数
	 */
	public long sweepDanglingRushParticipants(String couponId) {
		if (StringTools.isEmpty(couponId)) {
			return 0L;
		}
		String couponKey = Constants.REDIS_KEY_RUSHING_COUPON + couponId;
		long removed = 0L;
		List<String> batch = new ArrayList<>(SWEEP_BATCH_SIZE);
		ScanOptions options = ScanOptions.scanOptions().count(SWEEP_BATCH_SIZE).build();
		try (Cursor<String> cursor = stringRedisTemplate.opsForSet().scan(couponKey, options)) {
			while (cursor.hasNext()) {
				String member = cursor.next();
				if (StringTools.isEmpty(member)) {
					continue;
				}
				batch.add(member);
				if (batch.size() >= SWEEP_BATCH_SIZE) {
					removed += sweepBatch(couponId, batch);
					batch.clear();
				}
			}
		}
		if (!batch.isEmpty()) {
			removed += sweepBatch(couponId, batch);
		}
		if (removed > 0) {
			log.info("抢购参与者 SET 已清理僵尸成员 couponId={}, removed={}", couponId, removed);
		}
		return removed;
	}

	private long sweepBatch(String couponId, List<String> userIds) {
		String[] argv = new String[userIds.size() + 1];
		argv[0] = couponId;
		for (int i = 0; i < userIds.size(); i++) {
			argv[i + 1] = userIds.get(i);
		}
		Long removed = stringRedisTemplate.execute(
				RUSH_SWEEP_DANGLING_SCRIPT, Collections.emptyList(), (Object[]) argv);
		return removed == null ? 0L : removed;
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
