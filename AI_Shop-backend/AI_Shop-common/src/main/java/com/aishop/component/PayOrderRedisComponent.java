package com.aishop.component;

import com.aishop.constants.Constants;
import com.aishop.exception.BusinessException;
import com.aishop.exception.PayOrderLifecycleBusyException;
import com.aishop.redis.RedisUtils;
import com.aishop.support.PayOrderLifecycleLockHolder;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.TimeUnit;

/**
 * 支付订单生命周期的 Redis 标记与互斥锁。
 * <p>从 RedisComponent 拆出来的：支付回调、超时关单、迟到支付退款三条流程会并发操作同一笔支付单，
 * 靠这里的锁和一次性标记保证只有一条生效。这几个键的语义是互相咬合的，放一起才看得出全貌。
 * <p>标记只做"已处理过"的去重，不作为业务状态的依据 —— 状态以数据库为准。
 */
@Component("payOrderRedisComponent")
@Slf4j
public class PayOrderRedisComponent {

	@Resource
	private RedisUtils redisUtils;

	@Resource
	private StringRedisTemplate stringRedisTemplate;

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

	// 只有持锁者能删自己的锁：token 不匹配就不删，避免删掉别人续期后的锁
	private static final String PAY_ORDER_LIFECYCLE_UNLOCK_LUA =
			"if redis.call('get', KEYS[1]) == ARGV[1] then "
					+ "return redis.call('del', KEYS[1]) "
					+ "else return 0 end";

	private static final DefaultRedisScript<Long> PAY_ORDER_LIFECYCLE_UNLOCK_SCRIPT =
			new DefaultRedisScript<>(PAY_ORDER_LIFECYCLE_UNLOCK_LUA, Long.class);

	public void runWithPayOrderLifecycleLock(String payOrderId, Runnable action) {
		runWithPayOrderLifecycleLock(payOrderId, () -> {
			action.run();
			return null;
		});
	}

	public <T> T runWithPayOrderLifecycleLock(String payOrderId, Callable<T> action) {
		if (StringTools.isEmpty(payOrderId)) {
			try {
				return action.call();
			} catch (RuntimeException e) {
				throw e;
			} catch (Exception e) {
				throw new BusinessException("支付订单处理失败", e);
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
				throw new PayOrderLifecycleBusyException();
			}
			return action.call();
		} catch (InterruptedException e) {
			Thread.currentThread().interrupt();
			throw new BusinessException("支付订单处理被中断");
		} catch (RuntimeException e) {
			throw e;
		} catch (Exception e) {
			throw new BusinessException("支付订单处理失败", e);
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
		Long deleted = stringRedisTemplate.execute(
				PAY_ORDER_LIFECYCLE_UNLOCK_SCRIPT, List.of(lockKey), token);
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

	private boolean setIfAbsent(String key, String value, long timeout, TimeUnit unit) {
		return Boolean.TRUE.equals(stringRedisTemplate.opsForValue().setIfAbsent(key, value, timeout, unit));
	}
}
