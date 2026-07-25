package com.aishop.biz.impl;

import com.aishop.api.dto.CouponRushPrepareDTO;
import com.aishop.api.dto.DiscountCouponDTO;
import com.aishop.api.dto.PayInfoDTO;
import com.aishop.api.enums.CouponTypeEnum;
import com.aishop.api.enums.RushingCouponStatusEnum;
import com.aishop.api.enums.RushingStatusEnum;
import com.aishop.api.enums.UserCouponStatusEnum;
import com.aishop.api.support.OrderFeignSupport;
import com.aishop.api.support.UserFeignSupport;
import com.aishop.biz.DiscountCouponService;
import com.aishop.component.CouponRushStockService;
import com.aishop.component.DiscountCouponCacheComponent;
import com.aishop.component.CouponRushRedisComponent;
import com.aishop.entity.enums.DateTimePatternEnum;
import com.aishop.entity.enums.PageSize;
import com.aishop.entity.po.DiscountCoupon;
import com.aishop.entity.po.UserCoupon;
import com.aishop.entity.query.DiscountCouponQuery;
import com.aishop.entity.query.SimplePage;
import com.aishop.entity.query.UserCouponQuery;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.DiscountCouponMapper;
import com.aishop.mappers.UserCouponMapper;
import com.aishop.utils.DateUtil;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Service("discountCouponService")
@Slf4j
public class DiscountCouponServiceImpl implements DiscountCouponService {

	@Resource
	private DiscountCouponMapper<DiscountCoupon, DiscountCouponQuery> discountCouponMapper;
	@Resource
	private UserCouponMapper<UserCoupon, UserCouponQuery> userCouponMapper;
@Resource
private CouponRushRedisComponent couponRushRedisComponent;
	@Resource
	private RabbitTemplate rabbitTemplate;

	@Resource
	private OrderFeignSupport orderFeignSupport;
	@Resource
	private DiscountCouponCacheComponent discountCouponCacheComponent;
	@Resource
	private CouponRushStockService couponRushStockService;
	@Resource
	private UserFeignSupport userFeignSupport;

	@Override
	public List<DiscountCoupon> findListByParam(DiscountCouponQuery param) {
		return this.discountCouponMapper.selectList(param);
	}

	@Override
	public Integer findCountByParam(DiscountCouponQuery param) {
		return this.discountCouponMapper.selectCount(param);
	}

	@Override
	public PaginationResultVO<DiscountCoupon> findListByPage(DiscountCouponQuery param) {
		int count = this.findCountByParam(param);
		int pageSize = param.getPageSize() == null ? PageSize.SIZE15.getSize() : param.getPageSize();

		SimplePage page = new SimplePage(param.getPageNo(), count, pageSize);
		param.setSimplePage(page);
		List<DiscountCoupon> list = this.findListByParam(param);
		PaginationResultVO<DiscountCoupon> result = new PaginationResultVO(count, page.getPageSize(), page.getPageNo(), page.getPageTotal(), list);
		return result;
	}

	@Override
	public Integer add(DiscountCoupon bean) {
		return this.discountCouponMapper.insert(bean);
	}

	@Override
	public Integer addBatch(List<DiscountCoupon> listBean) {
		if (listBean == null || listBean.isEmpty()) {
			return 0;
		}
		return this.discountCouponMapper.insertBatch(listBean);
	}

	@Override
	public Integer addOrUpdateBatch(List<DiscountCoupon> listBean) {
		if (listBean == null || listBean.isEmpty()) {
			return 0;
		}
		return this.discountCouponMapper.insertOrUpdateBatch(listBean);
	}

	@Override
	public Integer updateByParam(DiscountCoupon bean, DiscountCouponQuery param) {
		StringTools.checkParam(param);
		return this.discountCouponMapper.updateByParam(bean, param);
	}

	@Override
	public Integer deleteByParam(DiscountCouponQuery param) {
		StringTools.checkParam(param);
		return this.discountCouponMapper.deleteByParam(param);
	}

	@Override
	public DiscountCoupon getDiscountCouponByCouponId(String couponId) {
		return discountCouponCacheComponent.getDetail(
				couponId,
				() -> this.discountCouponMapper.selectByCouponId(couponId)
		);
	}

	@Override
	public Integer updateDiscountCouponByCouponId(DiscountCoupon bean, String couponId) {
		Integer rows = this.discountCouponMapper.updateByCouponId(bean, couponId);
		if (rows != null && rows > 0) {
			discountCouponCacheComponent.invalidateAfterWrite(couponId);
		}
		return rows;
	}

	@Override
	public Integer deleteDiscountCouponByCouponId(String couponId) {
		Integer rows = this.discountCouponMapper.deleteByCouponId(couponId);
		if (rows != null && rows > 0) {
			discountCouponCacheComponent.invalidateAfterWrite(couponId);
		}
		return rows;
	}

	@Override
	public PaginationResultVO loadDiscountCoupon(Integer pageNo, Integer pageSize, String status, String keyword) {
		return discountCouponCacheComponent.getPlazaList(
				status,
				pageNo,
				pageSize,
				keyword,
				() -> loadDiscountCouponFromDb(pageNo, pageSize, status, keyword)
		);
	}

	private PaginationResultVO<DiscountCoupon> loadDiscountCouponFromDb(
			Integer pageNo, Integer pageSize, String status, String keyword) {
		String now = DateUtil.getTimeOnParttern(0, DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern());
		DiscountCouponQuery discountCouponQuery = new DiscountCouponQuery();
		discountCouponQuery.setRushingstatus(RushingCouponStatusEnum.YES.getStatus());
		discountCouponQuery.setPageNo(pageNo);
		discountCouponQuery.setPageSize(pageSize);
		discountCouponQuery.setCouponNameFuzzy(keyword);
		if (RushingStatusEnum.ALL.getType().equals(status)) {
			discountCouponQuery.setStatus(null);
		} else if (RushingStatusEnum.UPCOMING.getType().equals(status)) {
			discountCouponQuery.setRushingStartTimeEnd(now);
		} else if (RushingStatusEnum.ONGOING.getType().equals(status)) {
			discountCouponQuery.setRushingStartTimeStart(now);
			discountCouponQuery.setRushingEndTimeEnd(now);
		} else if (RushingStatusEnum.ENDED.getType().equals(status)) {
			discountCouponQuery.setRushingEndTimeStart(now);
		}
		return this.findListByPage(discountCouponQuery);
	}

	@Override
	public void fillHasBoughtForPlaza(String userId, List<DiscountCoupon> list) {
		if (StringTools.isEmpty(userId) || list == null || list.isEmpty()) {
			return;
		}
		UserCouponQuery userCouponQuery = new UserCouponQuery();
		userCouponQuery.setUserId(userId);
		List<UserCoupon> userCoupons = userCouponMapper.selectList(userCouponQuery);
		Map<String, List<UserCoupon>> byCouponId = new HashMap<>();
		if (userCoupons != null) {
			byCouponId = userCoupons.stream()
					.filter(uc -> !StringTools.isEmpty(uc.getCouponId()))
					.collect(Collectors.groupingBy(UserCoupon::getCouponId));
		}
		for (DiscountCoupon coupon : list) {
			if (coupon == null || StringTools.isEmpty(coupon.getCouponId())) {
				continue;
			}
			String couponId = coupon.getCouponId();
			boolean hasBought = false;
			List<UserCoupon> owned = byCouponId.get(couponId);
			if (owned != null) {
				for (UserCoupon uc : owned) {
					Integer st = uc.getStatus();
					if (st != null && !UserCouponStatusEnum.CANT.getStatus().equals(st)) {
						hasBought = true;
						break;
					}
				}
			}
			if (!hasBought && couponRushRedisComponent.isUserRushCouponParticipant(userId, couponId)) {
				hasBought = true;
			}
			coupon.setHasBought(hasBought);
		}
	}

	@Override
	public void saveDiscountCoupon(DiscountCouponDTO discountCoupon) {
		DiscountCoupon coupon = new DiscountCoupon();
		// 获取当前时间 yyyy-MM-dd HH:mm:ss
		String tempTime = DateUtil.getTimeOnParttern(0, DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern());
		Date now = DateUtil.parse(tempTime, DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern());
		// 编辑时保留已售份数，避免 remain=新总量 导致多卖
		Integer soldCount = null;
		// 如果没id就是新增，有id则是修改
		boolean isNew = StringTools.isEmpty(discountCoupon.getCouponId());
		if (isNew){
			// 新增
			coupon.setCouponId(StringTools.createCouponId());
			coupon.setCreateTime(now);
			coupon.setUpdateTime(now);
			coupon.setCouponType(discountCoupon.getCouponType());
			coupon.setCouponName(discountCoupon.getCouponName());
			coupon.setTotalCount(discountCoupon.getTotalCount());
			if (CouponTypeEnum.FULL.getStatus().equals(discountCoupon.getCouponType())){
				coupon.setThresholdAmount(discountCoupon.getThresholdAmount());
				coupon.setDiscountAmount(discountCoupon.getDiscountAmount());
			}else if (CouponTypeEnum.DISCOUNT.getStatus().equals(discountCoupon.getCouponType())){
				coupon.setThresholdAmount(discountCoupon.getThresholdAmount());
				coupon.setDiscountRate(discountCoupon.getDiscountRate());
			} else if (CouponTypeEnum.NOTHRESHOLD.getStatus().equals(discountCoupon.getCouponType())){
				coupon.setDiscountAmount(discountCoupon.getDiscountAmount());
			}
			coupon.setValidStartTime(DateUtil.parse(discountCoupon.getValidStartTime(), DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern()));
			coupon.setValidEndTime(DateUtil.parse(discountCoupon.getValidEndTime(), DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern()));
			if (RushingCouponStatusEnum.YES.getStatus().equals(discountCoupon.getRushingstatus())){
				coupon.setRushingstatus(RushingCouponStatusEnum.YES.getStatus());
				coupon.setRushingStartTime(DateUtil.parse(discountCoupon.getRushingStartTime(), DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern()));
				coupon.setRushingEndTime(DateUtil.parse(discountCoupon.getRushingEndTime(), DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern()));
			}
		} else {
			coupon = this.discountCouponMapper.selectByCouponId(discountCoupon.getCouponId());
			if (coupon == null) {
				throw new BusinessException("优惠卷不存在");
			}
			int oldTotal = coupon.getTotalCount() == null ? 0 : coupon.getTotalCount();
			int oldRemain = coupon.getRemainCount() == null ? 0 : coupon.getRemainCount();
			soldCount = Math.max(0, oldTotal - oldRemain);
		}
		coupon.setUpdateTime(now);
		coupon.setCouponType(discountCoupon.getCouponType());
		coupon.setCouponName(discountCoupon.getCouponName());
		coupon.setTotalCount(discountCoupon.getTotalCount());
		if (CouponTypeEnum.FULL.getStatus().equals(discountCoupon.getCouponType())){
			coupon.setThresholdAmount(discountCoupon.getThresholdAmount());
			coupon.setDiscountAmount(discountCoupon.getDiscountAmount());
			coupon.setDiscountRate(null);
		}else if (CouponTypeEnum.DISCOUNT.getStatus().equals(discountCoupon.getCouponType())){
			coupon.setThresholdAmount(discountCoupon.getThresholdAmount());
			coupon.setDiscountRate(discountCoupon.getDiscountRate());
			coupon.setDiscountAmount(null);
		} else if (CouponTypeEnum.NOTHRESHOLD.getStatus().equals(discountCoupon.getCouponType())){
			coupon.setDiscountAmount(discountCoupon.getDiscountAmount());
			coupon.setThresholdAmount(null);
			coupon.setDiscountRate(null);
		}
		if (soldCount != null) {
			int newTotal = coupon.getTotalCount() == null ? 0 : coupon.getTotalCount();
			coupon.setRemainCount(Math.max(0, newTotal - soldCount));
		} else {
			coupon.setRemainCount(coupon.getTotalCount());
		}
		coupon.setValidStartTime(DateUtil.parse(discountCoupon.getValidStartTime(), DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern()));
		coupon.setValidEndTime(DateUtil.parse(discountCoupon.getValidEndTime(), DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern()));
		if (RushingCouponStatusEnum.YES.getStatus().equals(discountCoupon.getRushingstatus())){
			coupon.setRushingstatus(RushingCouponStatusEnum.YES.getStatus());
			coupon.setRushingStartTime(DateUtil.parse(discountCoupon.getRushingStartTime(), DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern()));
			coupon.setRushingEndTime(DateUtil.parse(discountCoupon.getRushingEndTime(), DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern()));
		}else {
			coupon.setRushingstatus(RushingCouponStatusEnum.NO.getStatus());
			coupon.setRushingStartTime(null);
			coupon.setRushingEndTime(null);
		}
		discountCouponMapper.insertOrUpdate(coupon);
		if (RushingCouponStatusEnum.YES.getStatus().equals(coupon.getRushingstatus())) {
			couponRushStockService.warmupStock(coupon.getCouponId(), coupon.getRemainCount(), coupon.getTotalCount());
		}
		discountCouponCacheComponent.invalidateAfterWrite(coupon.getCouponId());
		if (RushingCouponStatusEnum.YES.getStatus().equals(coupon.getRushingstatus())) {
			discountCouponCacheComponent.warmPlazaListCache(
					(status, pageNo, pageSize, keyword) -> loadDiscountCouponFromDb(pageNo, pageSize, status, keyword)
			);
			// 新增秒杀券时给所有用户发通知
			if (isNew) {
				broadcastRushCouponNotification(coupon);
			}
		}
	}

	@Override
	public PaginationResultVO loadDiscountCoupon4Admin(Integer pageNo, Integer pageSize, String couponNameFuzzy, Integer couponType, Integer status) {
		DiscountCouponQuery discountCouponQuery = new DiscountCouponQuery();
		discountCouponQuery.setPageNo(pageNo);
		discountCouponQuery.setPageSize(pageSize);
		discountCouponQuery.setCouponNameFuzzy(couponNameFuzzy);
		discountCouponQuery.setCouponType(couponType);
		discountCouponQuery.setStatus(status);
		return this.findListByPage(discountCouponQuery);
	}

	@Override
	public CouponRushPrepareDTO rushCoupon(String userId, String couponId, String idempotencyKey) {
		return orderFeignSupport.prepareCouponRush(userId, couponId, idempotencyKey);
	}

	@Override
	public PayInfoDTO buyDiscountCoupon(
			String userId, String couponId, String payMethod, String idempotencyKey) {
		return orderFeignSupport.postCouponRushOrder(
				userId, couponId, payMethod, idempotencyKey);
	}

	@Override
	public void releaseRushRedisReserve(String couponId, String userId) {
		couponRushRedisComponent.rollbackRushRedisReserve(couponId, userId);
	}

	@Override
	public void releaseRushCouponReserve(String couponId, String userId) {
		couponRushStockService.releaseStockAfterDbRefund(couponId, userId);
		discountCouponCacheComponent.invalidateAfterWrite(couponId);
	}

	@Override
	public BigDecimal calcDiscountAmount(DiscountCoupon coupon, BigDecimal orderAmount) {
		if (coupon == null || orderAmount == null || orderAmount.compareTo(BigDecimal.ZERO) <= 0) {
			return BigDecimal.ZERO;
		}
		Date now = new Date();
		if (coupon.getValidStartTime() != null && now.before(coupon.getValidStartTime())) {
			throw new BusinessException("优惠券未到使用时间");
		}
		if (coupon.getValidEndTime() != null && now.after(coupon.getValidEndTime())) {
			throw new BusinessException("优惠券已过期");
		}
		Integer couponType = coupon.getCouponType();
		if (CouponTypeEnum.FULL.getStatus().equals(couponType)) {
			BigDecimal threshold = coupon.getThresholdAmount() == null ? BigDecimal.ZERO : coupon.getThresholdAmount();
			if (orderAmount.compareTo(threshold) < 0) {
				throw new BusinessException("未达到优惠券使用门槛");
			}
			return coupon.getDiscountAmount() == null ? BigDecimal.ZERO : coupon.getDiscountAmount();
		}
		if (CouponTypeEnum.DISCOUNT.getStatus().equals(couponType)) {
			BigDecimal threshold = coupon.getThresholdAmount() == null ? BigDecimal.ZERO : coupon.getThresholdAmount();
			if (orderAmount.compareTo(threshold) < 0) {
				throw new BusinessException("未达到优惠券使用门槛");
			}
			BigDecimal rate = coupon.getDiscountRate();
			if (rate == null || rate.compareTo(BigDecimal.ZERO) <= 0 || rate.compareTo(BigDecimal.ONE) > 0) {
				throw new BusinessException("优惠券折扣配置异常");
			}
			BigDecimal payAmount = orderAmount.multiply(rate).setScale(2, java.math.RoundingMode.HALF_UP);
			return orderAmount.subtract(payAmount);
		}
		if (CouponTypeEnum.NOTHRESHOLD.getStatus().equals(couponType)) {
			return coupon.getDiscountAmount() == null ? BigDecimal.ZERO : coupon.getDiscountAmount();
		}
		throw new BusinessException("不支持的优惠券类型");
	}

	private void broadcastRushCouponNotification(DiscountCoupon coupon) {
		try {
			List<String> userIds = userFeignSupport.listAllUserIds();
			if (userIds == null || userIds.isEmpty()) {
				return;
			}
			String title = "秒杀券上线";
			String content = coupon.getCouponName() + " 已上线，快去抢购！";
			String bizType = "rush_coupon";
			String bizId = coupon.getCouponId();

			for (String userId : userIds) {
				userFeignSupport.sendNotifyAsync(userId, title, content, bizType, bizId);
			}
			log.info("秒杀券 {} 已通过 RabbitMQ 通知 {} 个用户", coupon.getCouponId(), userIds.size());
		} catch (Exception e) {
			log.error("秒杀券通知广播失败: {}", coupon.getCouponId(), e);
		}
	}
}
