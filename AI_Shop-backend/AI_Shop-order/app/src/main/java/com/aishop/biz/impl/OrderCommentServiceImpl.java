package com.aishop.biz.impl;

import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.stream.Collectors;

import com.aishop.component.SensitiveWordFilter;
import com.aishop.api.support.UserFeignSupport;
import com.aishop.api.vo.UserBriefVO;
import com.aishop.api.enums.CommentStatusEnum;
import com.aishop.api.enums.OrderCommentStatusEnum;
import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.po.OrderItem;
import com.aishop.entity.query.OrderInfoQuery;
import com.aishop.exception.BusinessException;
import com.aishop.biz.OrderInfoService;
import com.aishop.mappers.OrderItemMapper;
import com.aishop.integration.CommerceOutcomeClient;
import jakarta.annotation.Resource;

import org.springframework.stereotype.Service;

import com.aishop.entity.enums.PageSize;
import com.aishop.entity.query.OrderCommentQuery;
import com.aishop.entity.po.OrderComment;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.api.vo.ProductCommentStatsVO;
import com.aishop.entity.query.SimplePage;
import com.aishop.mappers.OrderCommentMapper;
import com.aishop.mappers.OrderInfoMapper;
import com.aishop.biz.OrderCommentService;
import com.aishop.utils.FileUtils;
import com.aishop.utils.StringTools;
import org.springframework.transaction.annotation.Transactional;

@Service("orderCommentService")
public class OrderCommentServiceImpl implements OrderCommentService {

	@Resource
	private OrderCommentMapper<OrderComment, OrderCommentQuery> orderCommentMapper;

	@Resource
	private OrderInfoMapper<OrderInfo, OrderInfoQuery> orderInfoMapper;

	@Resource
	private OrderInfoService orderInfoService;

	@Resource
	private UserFeignSupport userFeignSupport;

	@Resource
	private OrderItemMapper<OrderItem, com.aishop.entity.query.OrderItemQuery> orderItemMapper;

	@Resource
	private SensitiveWordFilter sensitiveWordFilter;

	@Resource
	private CommerceOutcomeClient commerceOutcomeClient;

	@Override
	public List<OrderComment> findListByParam(OrderCommentQuery param) {
		List<OrderComment> list = this.orderCommentMapper.selectList(param);
		if (list != null && !list.isEmpty() && Boolean.TRUE.equals(param.getQueryUserInfo())) {
			enrichUserBrief(list);
		}
		return list;
	}

	@Override
	public Integer findCountByParam(OrderCommentQuery param) {
		return this.orderCommentMapper.selectCount(param);
	}

	@Override
	public PaginationResultVO<OrderComment> findListByPage(OrderCommentQuery param) {
		int count = this.findCountByParam(param);
		int pageSize = param.getPageSize() == null ? PageSize.SIZE15.getSize() : param.getPageSize();

		SimplePage page = new SimplePage(param.getPageNo(), count, pageSize);
		param.setSimplePage(page);
		List<OrderComment> list = this.findListByParam(param);
		if (list != null && !list.isEmpty() && Boolean.TRUE.equals(param.getQueryProduct())) {
			List<String> orderIds = list.stream().map(OrderComment::getOrderId).collect(Collectors.toList());
			List<OrderItem> allItems = orderItemMapper.selectByOrderIds(orderIds);
			if (allItems != null && !allItems.isEmpty()) {
				java.util.Map<String, List<OrderItem>> itemMap = allItems.stream()
					.collect(Collectors.groupingBy(OrderItem::getOrderId));
				for (OrderComment comment : list) {
					comment.setOrderItems(itemMap.get(comment.getOrderId()));
				}
			}
		}
		PaginationResultVO<OrderComment> result = new PaginationResultVO(count, page.getPageSize(), page.getPageNo(), page.getPageTotal(), list);
		return result;
	}

	@Override
	public Integer add(OrderComment bean) {
		return this.orderCommentMapper.insert(bean);
	}

	@Override
	public Integer addBatch(List<OrderComment> listBean) {
		if (listBean == null || listBean.isEmpty()) {
			return 0;
		}
		return this.orderCommentMapper.insertBatch(listBean);
	}

	@Override
	public Integer addOrUpdateBatch(List<OrderComment> listBean) {
		if (listBean == null || listBean.isEmpty()) {
			return 0;
		}
		return this.orderCommentMapper.insertOrUpdateBatch(listBean);
	}

	@Override
	public Integer updateByParam(OrderComment bean, OrderCommentQuery param) {
		StringTools.checkParam(param);
		return this.orderCommentMapper.updateByParam(bean, param);
	}

	@Override
	public Integer deleteByParam(OrderCommentQuery param) {
		StringTools.checkParam(param);
		return this.orderCommentMapper.deleteByParam(param);
	}

	@Override
	public OrderComment getOrderCommentByOrderId(String orderId) {
		return this.orderCommentMapper.selectByOrderId(orderId);
	}

	@Override
	public Integer updateOrderCommentByOrderId(OrderComment bean, String orderId) {
		return this.orderCommentMapper.updateByOrderId(bean, orderId);
	}

	@Override
	public Integer deleteOrderCommentByOrderId(String orderId) {
		return this.orderCommentMapper.deleteByOrderId(orderId);
	}

	@Override
	@Transactional(rollbackFor = Exception.class)
	public boolean postComment(String userId, String orderId, String commentContent, String commentImages, Integer star) {
		commentContent = sensitiveWordFilter.replaceSensitiveWords(commentContent);
		commentImages = sanitizeCommentImages(commentImages);
		boolean pendingImageReview = containsQuarantinePath(commentImages);
		Date commentTime = new Date();
		OrderInfoQuery orderInfoQuery = new OrderInfoQuery();
		orderInfoQuery.setOrderId(orderId);
		orderInfoQuery.setUserId(userId);
		orderInfoQuery.setQueryItems(true);
		List<OrderInfo> orderInfoList = orderInfoService.findListByParam(orderInfoQuery);
		OrderInfo orderInfo = orderInfoList.get(0) == null ? null : orderInfoList.get(0);
		if (orderInfo  == null) {
			throw new BusinessException("订单不存在");
		}
		if (!orderInfo.getUserId().equals(userId)) {
			throw new BusinessException("订单不存在");
		}
		if (!OrderCommentStatusEnum.NOT_EVALUATED.getStatus().equals(orderInfo.getCommentStatus())) {
			throw new BusinessException("订单已评价");
		}
		OrderComment orderComment = new OrderComment();
		orderComment.setOrderId(orderId);
		orderComment.setCommentContent(commentContent);
		orderComment.setCommentImages(commentImages);
		orderComment.setStar(star);
		orderComment.setProductId(orderInfo.getOrderItemList().get(0).getProductId());
		orderComment.setCommentTime(commentTime);
		orderComment.setUserId(userId);
		orderComment.setPropertyInfo(orderInfo.getOrderItemList().get(0).getPropertyInfo());
		orderComment.setStatus(pendingImageReview
				? CommentStatusEnum.PENDING.getStatus()
				: CommentStatusEnum.NORMAL.getStatus());
		int marked = orderInfoMapper.markCommentEvaluatedIfNotEvaluated(orderId, userId);
		if (marked != 1) {
			throw new BusinessException("订单已评价");
		}
		Integer count = this.add(orderComment);
		if (count != 1) {
			throw new BusinessException("该订单已评价");
		}
		OrderItem reviewedItem = orderInfo.getOrderItemList().get(0);
		Map<String, Object> outcomePayload = new LinkedHashMap<>();
		outcomePayload.put("rating", star);
		outcomePayload.put("sentiment", star >= 4 ? "POSITIVE" : star <= 2 ? "NEGATIVE" : "NEUTRAL");
		outcomePayload.put("hasMedia", !StringTools.isEmpty(commentImages));
		commerceOutcomeClient.recordAfterCommit(CommerceOutcomeClient.fromVerifiedCarrier(
				CommerceOutcomeClient.stableEventId("review", orderId, reviewedItem.getProductId()),
				"REVIEW",
				CommerceOutcomeClient.stableIdempotencyKey(
						"review", orderId, reviewedItem.getProductId()),
				"REVIEW",
				userId,
				reviewedItem,
				reviewedItem.getPropertyValueIdHash(),
				orderId,
				outcomePayload,
				commentTime));
		return pendingImageReview;
	}

	@Override
	public int deletePendingCommentByOrderId(String orderId) {
		return orderCommentMapper.deleteByOrderIdIfStatus(orderId, CommentStatusEnum.PENDING.getStatus());
	}

	@Override
	public int publishPendingComment(String orderId, String commentImages) {
		return orderCommentMapper.publishIfStatus(
				orderId,
				commentImages,
				CommentStatusEnum.PENDING.getStatus(),
				CommentStatusEnum.NORMAL.getStatus());
	}

	@Override
	public OrderComment getComment(String userId, String orderId) {
		OrderInfoQuery orderInfoQuery = new OrderInfoQuery();
		orderInfoQuery.setOrderId(orderId);
		List<OrderInfo> orderInfoList = orderInfoService.findListByParam(orderInfoQuery);
		OrderInfo orderInfo = orderInfoList.get(0) == null ? null : orderInfoList.get(0);
		if (orderInfo  == null) {
			throw new BusinessException("订单不存在");
		}
		if (!orderInfo.getUserId().equals(userId) && userId != null) {
			throw new BusinessException("订单不存在");
		}
		// 不查询已删除的评价（含商品信息）
		OrderCommentQuery commentQuery = new OrderCommentQuery();
		commentQuery.setOrderId(orderId);
		commentQuery.setQueryProduct(true);
		commentQuery.setStatus(CommentStatusEnum.NORMAL.getStatus());
		List<OrderComment> commentList = this.findListByParam(commentQuery);
		OrderComment orderComment = commentList.isEmpty() ? null : commentList.get(0);
		if ((orderComment == null || !CommentStatusEnum.NORMAL.getStatus().equals(orderComment.getStatus()))
				&& userId != null){
			return null;
		}
		return orderComment;
	}

	@Override
	@Transactional(rollbackFor = Exception.class)
	public void postReComment(String userId, String orderId, String reCommentContent, String reCommentImages) {
		// 敏感词过滤
		reCommentContent = sensitiveWordFilter.replaceSensitiveWords(reCommentContent);
		reCommentImages = sanitizeCommentImages(reCommentImages);
		Date reCommentTime = new Date();
		OrderInfoQuery orderInfoQuery = new OrderInfoQuery();
		orderInfoQuery.setOrderId(orderId);
		orderInfoQuery.setUserId(userId);
		List<OrderInfo> orderInfoList = orderInfoService.findListByParam(orderInfoQuery);
		OrderInfo orderInfo = orderInfoList.get(0) == null ? null : orderInfoList.get(0);
		if (orderInfo  == null) {
			throw new BusinessException("订单不存在");
		}
		if (!orderInfo.getUserId().equals(userId)) {
			throw new BusinessException("订单不存在");
		}
		// 判断订单是否已经追评
		if (OrderCommentStatusEnum.ADDITIONAL_EVALUATED.getStatus().equals(orderInfo.getCommentStatus())){
			throw new BusinessException("该订单已追评");
		}
		// 只有已评论状态才可追评
		if (!OrderCommentStatusEnum.EVALUATED.getStatus().equals(orderInfo.getCommentStatus())) {
			throw new BusinessException("订单未评价，无法追评");
		}
		OrderComment orderComment = this.getOrderCommentByOrderId(orderId);
		orderComment.setRecommentContent(reCommentContent);
		orderComment.setRecommentImages(reCommentImages);
		orderComment.setRecommentTime(reCommentTime);
		// 更改OrderInfo状态和评论状态
		orderInfo.setCommentStatus(OrderCommentStatusEnum.ADDITIONAL_EVALUATED.getStatus());
		// 统一更新
		orderInfoService.updateByParam(orderInfo, orderInfoQuery);
		Integer count = this.updateOrderCommentByOrderId(orderComment, orderId);
		if (count != 1) {
			throw new BusinessException("该订单已追评");
		}
	}

	@Override
	@Transactional(rollbackFor = Exception.class)
	public void delMyComment(String userId, String orderId) {
		OrderInfoQuery orderInfoQuery = new OrderInfoQuery();
		orderInfoQuery.setOrderId(orderId);
		List<OrderInfo> orderInfoList = orderInfoService.findListByParam(orderInfoQuery);
		OrderInfo orderInfo = orderInfoList.get(0) == null ? null : orderInfoList.get(0);
		if (orderInfo  == null) {
			throw new BusinessException("订单不存在");
		}
		if (!orderInfo.getUserId().equals(userId) && userId != null) {
			throw new BusinessException("订单不存在");
		}
		if (!OrderCommentStatusEnum.EVALUATED.getStatus().equals(orderInfo.getCommentStatus()) &&
			!OrderCommentStatusEnum.ADDITIONAL_EVALUATED.getStatus().equals(orderInfo.getCommentStatus())) {
			throw new BusinessException("订单未评价，无法删除");
		}
		// 将评论状态设为已删除，逻辑删除
		OrderComment orderComment = getOrderCommentByOrderId(orderId);
		if (orderComment == null){
			throw new BusinessException("该订单未评价");
		}
		if (CommentStatusEnum.DEL.getStatus().equals(orderComment.getStatus())){
			throw new BusinessException("该评价已删除");
		}
		orderComment.setStatus(CommentStatusEnum.DEL.getStatus());
		Integer count = this.updateOrderCommentByOrderId(orderComment, orderId);
		if (count != 1) {
			throw new BusinessException("该订单已删除");
		}
	}

	@Override
	public void bizComment(String orderId, String commentContent, String commentImages) {
		// 如果订单为未评价则无法评价
		if (Objects.equals(orderInfoService.getOrderInfoByOrderId(orderId).getCommentStatus(), OrderCommentStatusEnum.NOT_EVALUATED.getStatus())){
			throw new BusinessException("订单未评价，无法评价");
		}
		OrderComment orderComment = orderCommentMapper.selectByOrderId(orderId);
		orderComment.setCommentBizReply(commentContent);
		orderCommentMapper.insertOrUpdate(orderComment);
	}

	@Override
	public PaginationResultVO<OrderComment> findByNickNameFuzzy(PaginationResultVO<OrderComment> resultVO, String nickNameFuzzy) {
		List<OrderComment> orderCommentList = resultVO.getList();
		List<OrderComment> newOrderCommentList = new ArrayList<>();
		for (OrderComment orderComment : orderCommentList){
			if (orderComment.getNickName().contains(nickNameFuzzy)){
				newOrderCommentList.add(orderComment);
			}
		}
		resultVO.setList(newOrderCommentList);
		return resultVO;
	}

	@Override
	public PaginationResultVO<OrderComment> findByProductNameFuzzy(PaginationResultVO<OrderComment> resultVO, String productNameFuzzy) {
		List<OrderComment> orderCommentList = resultVO.getList();
		List<OrderComment> newOrderCommentList = new ArrayList<>();
		for (OrderComment orderComment : orderCommentList){
			if (orderComment.getProductName().contains(productNameFuzzy)){
				newOrderCommentList.add(orderComment);
			}
		}
		resultVO.setList(newOrderCommentList);
		return resultVO;
	}

	@Override
	public ProductCommentStatsVO getProductCommentStats(String productId) {
		ProductCommentStatsVO vo = new ProductCommentStatsVO();
		vo.setTotalCount(0);
		vo.setGoodCount(0);
		vo.setImageCount(0);
		vo.setGoodRatePercent(100);
		if (StringTools.isEmpty(productId)) {
			return vo;
		}
		java.util.Map<String, Object> row = orderCommentMapper.selectProductCommentStats(productId);
		if (row == null || row.isEmpty()) {
			return vo;
		}
		int total = toInt(row.get("totalCount"));
		int good = toInt(row.get("goodCount"));
		int image = toInt(row.get("imageCount"));
		vo.setTotalCount(total);
		vo.setGoodCount(good);
		vo.setImageCount(image);
		vo.setGoodRatePercent(total <= 0 ? 100 : Math.min(100, Math.round(good * 100f / total)));
		return vo;
	}

	private static int toInt(Object v) {
		if (v == null) {
			return 0;
		}
		if (v instanceof Number) {
			return ((Number) v).intValue();
		}
		try {
			return Integer.parseInt(String.valueOf(v));
		} catch (NumberFormatException e) {
			return 0;
		}
	}

	private String sanitizeCommentImages(String commentImages) {
		if (StringTools.isEmpty(commentImages)) {
			return commentImages;
		}
		if (commentImages.contains("[object Object]")) {
			throw new BusinessException("评论图片格式异常，请重新上传后再提交");
		}
		List<String> paths = splitImagePaths(commentImages);
		if (paths.isEmpty()) {
			return null;
		}
		for (String path : paths) {
			if (!StringTools.pathIsOK(path)) {
				throw new BusinessException("评论图片路径无效，请重新上传");
			}
		}
		return String.join(",", paths);
	}

	private static boolean containsQuarantinePath(String commentImages) {
		return splitImagePaths(commentImages).stream().anyMatch(FileUtils::isModerationQuarantinePath);
	}

	private static List<String> splitImagePaths(String commentImages) {
		if (StringTools.isEmpty(commentImages)) {
			return List.of();
		}
		List<String> paths = new ArrayList<>();
		for (String part : commentImages.split(",")) {
			String path = part == null ? null : part.trim();
			if (!StringTools.isEmpty(path)) {
				paths.add(path);
			}
		}
		return paths;
	}

	private void enrichUserBrief(List<OrderComment> list) {
		List<String> userIds = list.stream()
				.map(OrderComment::getUserId)
				.filter(id -> !StringTools.isEmpty(id))
				.distinct()
				.collect(Collectors.toList());
		Map<String, UserBriefVO> map = userFeignSupport.mapBriefByUserIds(userIds);
		for (OrderComment comment : list) {
			UserBriefVO brief = map.get(comment.getUserId());
			if (brief != null) {
				comment.setNickName(brief.getNickName());
				comment.setAvatar(brief.getAvatar());
			}
		}
	}

}
