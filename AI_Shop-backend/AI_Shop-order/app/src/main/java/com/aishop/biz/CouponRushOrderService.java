package com.aishop.biz;

import com.aishop.api.dto.CouponRushPrepareDTO;
import com.aishop.api.dto.PayInfoDTO;
import jakarta.validation.constraints.NotEmpty;

/**
 * 优惠券秒杀下单。
 * <p>从 OrderInfoServiceImpl 拆出来的：秒杀和普通下单只共用"订单"这张表，
 * 校验、预占、建单、支付、券激活全是另一套规则，混在一个类里让下单主流程更难读。
 * <p>OrderInfoService 上的同名方法保留为委托，调用方（含 Feign）不受影响。
 */
public interface CouponRushOrderService {

	/**
	 * 预占抢购资格并建单。Redis 预占成功后才扣库存建单，任一步失败都要回滚 Redis 预占。
	 */
	CouponRushPrepareDTO prepareCouponRush(
			@NotEmpty String userId,
			@NotEmpty String couponId,
			@NotEmpty String idempotencyKey);

	/**
	 * 对已预占的抢购单发起支付。
	 */
	PayInfoDTO postCouponRushOrder(
			@NotEmpty String userId,
			@NotEmpty String couponId,
			@NotEmpty String payMethod,
			@NotEmpty String idempotencyKey);

	/**
	 * 补偿：把已支付秒杀订单对应的用户券从"不可用"改为"未使用"。
	 * <p>支付回调里激活失败时，用户手里会留着一张永久不可用的券，靠这个入口兜回来。
	 */
	void syncPaidCouponRushUserCoupons(@NotEmpty String userId);

	/**
	 * 激活单张秒杀用户券，已激活时静默返回。支付成功流程会调用。
	 */
	void activateUserCoupon(String userId, String userCouponId);
}
