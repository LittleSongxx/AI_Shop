package com.aishop.biz.impl;

import com.aishop.api.dto.CouponRushPrepareDTO;
import com.aishop.api.dto.PayInfoDTO;
import com.aishop.api.dto.PayOrderMessageDTO;
import com.aishop.api.dto.UserCouponCreateDTO;
import com.aishop.api.enums.OrderCommentStatusEnum;
import com.aishop.api.enums.OrderFromTypeEnum;
import com.aishop.api.enums.OrderItemStatusEnum;
import com.aishop.api.enums.OrderStatusEnum;
import com.aishop.api.enums.PayChannelEnum;
import com.aishop.api.enums.RushingCouponStatusEnum;
import com.aishop.api.enums.UserCouponStatusEnum;
import com.aishop.api.support.CouponFeignSupport;
import com.aishop.api.support.PayFeignSupport;
import com.aishop.api.vo.DiscountCouponVO;
import com.aishop.api.vo.UserCouponVO;
import com.aishop.biz.CouponRushOrderService;
import com.aishop.biz.OrderRequestIdempotencyService;
import com.aishop.component.CouponRushRedisComponent;
import com.aishop.constants.Constants;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.TransactionalMqSender;
import com.aishop.entity.config.AppConfig;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.entity.po.OrderCouponRel;
import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.po.OrderItem;
import com.aishop.entity.query.OrderCouponRelQuery;
import com.aishop.entity.query.OrderInfoQuery;
import com.aishop.entity.query.OrderItemQuery;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.OrderCouponRelMapper;
import com.aishop.mappers.OrderInfoMapper;
import com.aishop.mappers.OrderItemMapper;
import com.aishop.support.MqIdempotencyKeys;
import com.aishop.utils.OrderPayAmountUtil;
import com.aishop.utils.StringTools;
import io.seata.spring.annotation.GlobalTransactional;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.Date;
import java.util.List;
import java.util.Map;

/**
 * 优惠券秒杀下单实现。
 * <p>Redis 预占是资格的唯一来源：预占成功后的任何一步失败，都必须把预占放回去，
 * 否则库存会被一直挂在一个不存在的订单上。下面每处 releaseRush* 调用都是为了这个。
 */
@Service("couponRushOrderService")
@Slf4j
public class CouponRushOrderServiceImpl implements CouponRushOrderService {

	@Resource
	private OrderInfoMapper<OrderInfo, OrderInfoQuery> orderInfoMapper;

	@Resource
	private OrderItemMapper<OrderItem, OrderItemQuery> orderItemMapper;

	@Resource
	private OrderCouponRelMapper<OrderCouponRel, OrderCouponRelQuery> orderCouponRelMapper;

	@Resource
	private CouponFeignSupport couponFeignSupport;

	@Resource
	private PayFeignSupport payFeignSupport;

@Resource
private CouponRushRedisComponent couponRushRedisComponent;

	@Resource
	private AppConfig appConfig;

	@Resource
	private TransactionalMqSender transactionalMqSender;

	@Resource
	private OrderRequestIdempotencyService orderRequestIdempotencyService;

	@Override
	@GlobalTransactional(name = "aishop-coupon-rush-prepare", rollbackFor = Exception.class)
	@Transactional(rollbackFor = Exception.class)
	public CouponRushPrepareDTO prepareCouponRush(String userId, String couponId, String idempotencyKey) {
		return orderRequestIdempotencyService.execute(
				userId,
				OrderRequestIdempotencyService.COMMAND_COUPON_RUSH_PREPARE,
				idempotencyKey,
				Map.of("couponId", couponId),
				CouponRushPrepareDTO.class,
				() -> createCouponRushReservation(userId, couponId));
	}

	@Override
	@GlobalTransactional(name = "aishop-coupon-rush-pay", rollbackFor = Exception.class)
	@Transactional(rollbackFor = Exception.class)
	public PayInfoDTO postCouponRushOrder(String userId, String couponId, String payMethod, String idempotencyKey) {
		return orderRequestIdempotencyService.execute(
				userId,
				OrderRequestIdempotencyService.COMMAND_COUPON_RUSH_PAY,
				idempotencyKey,
				Map.of("couponId", couponId, "payMethod", payMethod),
				PayInfoDTO.class,
				() -> createCouponRushPayment(userId, couponId, payMethod));
	}

	private CouponRushPrepareDTO createCouponRushReservation(String userId, String couponId) {
		DiscountCouponVO discountCoupon = validateCouponRush(couponId);
		String userCouponId = StringTools.createUserCouponId();
		BigDecimal payAmount = new BigDecimal(Constants.RUSHING_COUPON_PAY_AMOUNT);

		int rushCode = couponRushRedisComponent.rushingCoupon(couponId, userId, userCouponId);
		if (rushCode == 1) {
			couponFeignSupport.syncRushStockFromDbIfRedisZero(couponId);
			throw new BusinessException("库存不足！");
		}
		assertRushCode(rushCode);

		try {
			DiscountCouponVO lockedCoupon = couponFeignSupport.getCoupon(couponId);
			boolean unlimited = lockedCoupon != null && lockedCoupon.isUnlimitedStock();
			if (lockedCoupon == null
					|| (!unlimited && (lockedCoupon.getRemainCount() == null || lockedCoupon.getRemainCount() <= 0))) {
				couponFeignSupport.releaseRushRedisReserve(couponId, userId);
				throw new BusinessException("库存不足");
			}

			int affected = couponFeignSupport.deductStock(couponId);
			if (affected == 0) {
				couponFeignSupport.releaseRushRedisReserve(couponId, userId);
				throw new BusinessException("库存不足或并发冲突");
			}
			couponFeignSupport.invalidateCouponCache(couponId);

			OrderInfo orderInfo;
			try {
				orderInfo = createCouponRushOrder(userId, couponId, userCouponId, discountCoupon);
			} catch (Exception e) {
				couponFeignSupport.releaseRushCouponReserve(couponId, userId);
				if (e instanceof BusinessException) {
					throw (BusinessException) e;
				}
				log.error("优惠券秒杀建单失败 couponId={}, userId={}", couponId, userId, e);
				throw new BusinessException("下单失败，请稍后重试");
			}

			long payExpireAt = orderInfo.getOrderTime().getTime() + appConfig.getOrderExpireMinute() * 60 * 1000L;

			CouponRushPrepareDTO dto = new CouponRushPrepareDTO();
			dto.setCouponId(couponId);
			dto.setUserCouponId(userCouponId);
			dto.setCouponName(discountCoupon.getCouponName());
			dto.setPayAmount(payAmount);
			dto.setOrderId(orderInfo.getOrderId());
			dto.setPayOrderId(orderInfo.getPayOrderId());
			dto.setPayExpireAt(payExpireAt);
			log.info("优惠券秒杀预占并建单成功 couponId={}, orderId={}", couponId, orderInfo.getOrderId());
			return dto;
		} finally {
			couponFeignSupport.syncRushStockFromDbIfRedisZero(couponId);
		}
	}

	private PayInfoDTO createCouponRushPayment(String userId, String couponId, String payMethod) {
		PayChannelEnum payChannelEnum = PayChannelEnum.resolve(payMethod);
		if (payChannelEnum == null) {
			throw new BusinessException("支付方式无效");
		}
		couponFeignSupport.assertRushNotBlocked(couponId);

		String userCouponId = couponRushRedisComponent.getRushUserCouponId(userId, couponId);
		if (StringTools.isEmpty(userCouponId)) {
			throw new BusinessException("抢购资格已失效，请返回重新抢购");
		}

		DiscountCouponVO discountCoupon = couponFeignSupport.getCoupon(couponId);
		if (discountCoupon == null) {
			throw new BusinessException("优惠券不存在");
		}

		OrderInfo orderInfo = findCouponRushOrderByUserCoupon(userCouponId);
		if (orderInfo == null || !userId.equals(orderInfo.getUserId())) {
			throw new BusinessException("订单不存在，请返回重新抢购");
		}
		if (!OrderStatusEnum.WAIT_PAYMENT.getStatus().equals(orderInfo.getOrderStatus())) {
			if (OrderStatusEnum.CLOSED.getStatus().equals(orderInfo.getOrderStatus())
					|| OrderStatusEnum.CANCELLED.getStatus().equals(orderInfo.getOrderStatus())) {
				throw new BusinessException("订单已关闭，请重新抢购");
			}
			throw new BusinessException("当前订单已支付");
		}

		OrderInfo updatePay = new OrderInfo();
		updatePay.setPayChannel(payMethod);
		OrderInfoQuery payUpdateQuery = new OrderInfoQuery();
		payUpdateQuery.setOrderId(orderInfo.getOrderId());
		payUpdateQuery.setOrderStatus(OrderStatusEnum.WAIT_PAYMENT.getStatus());
		orderInfoMapper.updateByParam(updatePay, payUpdateQuery);

		orderInfo.setPayChannel(payMethod);
		BigDecimal payAmount = OrderPayAmountUtil.normalizeChannelPayAmount(orderInfo.getAmount());
		PayInfoDTO payInfoDTO = payFeignSupport.getPayUrl(
				payChannelEnum.getPayScene(), orderInfo.getPayOrderId(), orderInfo.getSubject(), payAmount);
		payInfoDTO.setOrderId(orderInfo.getOrderId());
		payFeignSupport.createPending(userId, orderInfo.getPayOrderId(), orderInfo.getOrderId(),
				payAmount, payMethod);
		log.info("优惠券秒杀发起支付 orderId={}, payOrderId={}", orderInfo.getOrderId(), orderInfo.getPayOrderId());
		return payInfoDTO;
	}

	private DiscountCouponVO validateCouponRush(String couponId) {
		DiscountCouponVO discountCoupon = couponFeignSupport.getCoupon(couponId);
		if (discountCoupon == null) {
			throw new BusinessException("优惠券不存在");
		}
		if (RushingCouponStatusEnum.NO.getStatus().equals(discountCoupon.getRushingstatus())) {
			throw new BusinessException("该优惠券不是抢购状态");
		}
		Date now = new Date();
		if (discountCoupon.getRushingStartTime() != null && now.before(discountCoupon.getRushingStartTime())) {
			throw new BusinessException("该优惠券未开始抢购");
		}
		if (discountCoupon.getRushingEndTime() != null && now.after(discountCoupon.getRushingEndTime())) {
			throw new BusinessException("该优惠券已结束抢购");
		}
		couponFeignSupport.assertRushNotBlocked(couponId);
		if (!couponFeignSupport.hasAvailableRushStock(couponId)) {
			couponFeignSupport.syncRushStockFromDbIfRedisZero(couponId);
			if (!couponFeignSupport.hasAvailableRushStock(couponId)) {
				throw new BusinessException("库存不足！");
			}
		}
		return discountCoupon;
	}

	/**
	 * Lua 预占脚本的返回码，含义由 RedisComponent#rushingCoupon 定义。
	 */
	private void assertRushCode(int rushCode) {
		if (rushCode == 1) {
			throw new BusinessException("库存不足！");
		}
		if (rushCode == 2) {
			throw new BusinessException("不能重复下单！");
		}
		if (rushCode == 3) {
			throw new BusinessException("网络异常，请稍后再试~");
		}
	}

	private String buildCouponRushOrderSubject(String couponName) {
		String name = StringTools.isEmpty(couponName) ? "优惠券" : couponName.trim();
		return Constants.COUPON_RUSH_ORDER_SUBJECT_PREFIX + name;
	}

	private OrderInfo createCouponRushOrder(String userId, String couponId, String userCouponId,
			DiscountCouponVO discountCoupon) {
		Date now = new Date();
		String orderId = StringTools.createOrderId();
		String payOrderId = StringTools.createPayOrderId();
		BigDecimal payAmount = new BigDecimal(Constants.RUSHING_COUPON_PAY_AMOUNT);

		OrderInfo orderInfo = new OrderInfo();
		orderInfo.setOrderId(orderId);
		orderInfo.setAmount(payAmount);
		orderInfo.setUserId(userId);
		orderInfo.setOrderTime(now);
		orderInfo.setOrderStatus(OrderStatusEnum.WAIT_PAYMENT.getStatus());
		orderInfo.setCommentStatus(OrderCommentStatusEnum.NOT_EVALUATED.getStatus());
		orderInfo.setPayScene(String.valueOf(OrderFromTypeEnum.COUPON.getType()));
		orderInfo.setPayChannel(PayChannelEnum.ALIPAY_PC.getPayScene());
		orderInfo.setPayOrderId(payOrderId);
		orderInfo.setSubject(buildCouponRushOrderSubject(discountCoupon.getCouponName()));
		orderInfoMapper.insert(orderInfo);

		// 券先建成"不可用"，支付成功后才激活成"未使用"，避免没付钱就能用。
		UserCouponCreateDTO createDTO = new UserCouponCreateDTO();
		createDTO.setUserCouponId(userCouponId);
		createDTO.setUserId(userId);
		createDTO.setCouponId(couponId);
		createDTO.setStatus(UserCouponStatusEnum.CANT.getStatus());
		couponFeignSupport.createUserCoupon(createDTO);

		OrderCouponRel rel = new OrderCouponRel();
		rel.setOrderId(orderId);
		rel.setUserCouponId(userCouponId);
		rel.setCouponId(couponId);
		rel.setDiscountAmount(payAmount);
		rel.setCreateTime(now);
		orderCouponRelMapper.insert(rel);

		OrderItem orderItem = new OrderItem();
		orderItem.setOrderItemId(orderId + "_1");
		orderItem.setOrderId(orderId);
		orderItem.setProductId(couponId);
		orderItem.setProductName(discountCoupon.getCouponName());
		orderItem.setPropertyValueIdHash("coupon_rush");
		orderItem.setPropertyInfo("优惠券秒杀");
		orderItem.setBuyCount(1);
		orderItem.setItemAmount(payAmount);
		orderItem.setOrderItemStatus(OrderItemStatusEnum.NORMAL.getStatus());
		orderItem.setCover("");
		orderItemMapper.insert(orderItem);

		PayOrderMessageDTO payTimeoutDto = new PayOrderMessageDTO();
		payTimeoutDto.setOrderId(orderId);
		transactionalMqSender.sendAfterCommit(
				RabbitMQConfig.PAY_EXCHANGE,
				RabbitMQConfig.PAY_TIMEOUT_DELAY_KEY,
				payTimeoutDto,
				MqIdempotencyKeys.payTimeout(orderId),
				MessageReliabilityLevelEnum.STANDARD);
		log.info("优惠券秒杀订单已创建 orderId={}, payOrderId={}", orderId, payOrderId);
		return orderInfo;
	}

	private OrderInfo findCouponRushOrderByUserCoupon(String userCouponId) {
		if (StringTools.isEmpty(userCouponId)) {
			return null;
		}
		OrderCouponRelQuery relQuery = new OrderCouponRelQuery();
		relQuery.setUserCouponId(userCouponId);
		List<OrderCouponRel> rels = orderCouponRelMapper.selectList(relQuery);
		if (rels == null || rels.isEmpty()) {
			return null;
		}
		return orderInfoMapper.selectByOrderId(rels.get(0).getOrderId());
	}

	@Override
	public void activateUserCoupon(String userId, String userCouponId) {
		if (StringTools.isEmpty(userCouponId) || StringTools.isEmpty(userId)) {
			return;
		}
		try {
			couponFeignSupport.changeUserCouponStatus(userCouponId, userId,
					UserCouponStatusEnum.CANT.getStatus(), UserCouponStatusEnum.NOUSE.getStatus(), null);
		} catch (BusinessException ignore) {
			// 幂等：已激活则忽略
		}
	}

	@Override
	public void syncPaidCouponRushUserCoupons(String userId) {
		OrderInfoQuery orderQuery = new OrderInfoQuery();
		orderQuery.setUserId(userId);
		orderQuery.setPayScene(String.valueOf(OrderFromTypeEnum.COUPON.getType()));
		orderQuery.setOrderStatusList(new Integer[]{
				OrderStatusEnum.PAID.getStatus(),
				OrderStatusEnum.SHIPPED.getStatus(),
				OrderStatusEnum.COMPLETED.getStatus()
		});
		List<OrderInfo> orders = orderInfoMapper.selectList(orderQuery);
		if (orders == null || orders.isEmpty()) {
			return;
		}
		int activated = 0;
		for (OrderInfo orderInfo : orders) {
			OrderCouponRelQuery relQuery = new OrderCouponRelQuery();
			relQuery.setOrderId(orderInfo.getOrderId());
			List<OrderCouponRel> rels = orderCouponRelMapper.selectList(relQuery);
			for (OrderCouponRel rel : rels) {
				if (StringTools.isEmpty(rel.getUserCouponId())) {
					continue;
				}
				UserCouponVO uc = couponFeignSupport.getUserCoupon(rel.getUserCouponId());
				if (uc != null && UserCouponStatusEnum.CANT.getStatus().equals(uc.getStatus())) {
					activateUserCoupon(orderInfo.getUserId(), rel.getUserCouponId());
					activated++;
				}
			}
		}
		if (activated > 0) {
			log.info("同步秒杀已付订单用户券为未使用 userId={}, count={}", userId, activated);
		}
	}
}
