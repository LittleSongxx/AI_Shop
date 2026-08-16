package com.aishop.biz.impl;

import java.util.ArrayList;
import java.util.Date;
import java.util.List;

import com.aishop.component.OrderNotificationPublisher;
import com.aishop.constants.RabbitMQConfig;
import com.aishop.constants.TransactionalMqSender;
import com.aishop.api.dto.PayOrderMessageDTO;
import com.aishop.entity.enums.MessageReliabilityLevelEnum;
import com.aishop.api.enums.LogisticsStatusEnum;
import com.aishop.api.enums.OrderStatusEnum;
import com.aishop.entity.po.OrderInfo;
import com.aishop.entity.po.OrderLogisticsInfoRecord;
import com.aishop.entity.query.OrderInfoQuery;
import com.aishop.entity.query.OrderLogisticsInfoRecordQuery;
import com.aishop.exception.BusinessException;
import com.aishop.biz.OrderInfoService;
import com.aishop.biz.OrderLogisticsInfoRecordService;
import com.aishop.support.MqIdempotencyKeys;
import jakarta.annotation.Resource;

import org.springframework.stereotype.Service;

import com.aishop.entity.enums.PageSize;
import com.aishop.entity.query.OrderLogisticsInfoQuery;
import com.aishop.entity.po.OrderLogisticsInfo;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.entity.query.SimplePage;
import com.aishop.mappers.OrderLogisticsInfoMapper;
import com.aishop.biz.OrderLogisticsInfoService;
import com.aishop.utils.StringTools;
import org.springframework.transaction.annotation.Transactional;

@Service("orderLogisticsInfoService")
public class OrderLogisticsInfoServiceImpl implements OrderLogisticsInfoService {

	@Resource
	private OrderLogisticsInfoMapper<OrderLogisticsInfo, OrderLogisticsInfoQuery> orderLogisticsInfoMapper;

	@Resource
	private OrderLogisticsInfoRecordService orderLogisticsInfoRecordService;

	@Resource
	private OrderInfoService orderInfoService;

	@Resource
	private OrderNotificationPublisher orderNotificationPublisher;

	@Resource
	private TransactionalMqSender transactionalMqSender;

	@Override
	public List<OrderLogisticsInfo> findListByParam(OrderLogisticsInfoQuery param) {
		return this.orderLogisticsInfoMapper.selectList(param);
	}

	@Override
	public Integer findCountByParam(OrderLogisticsInfoQuery param) {
		return this.orderLogisticsInfoMapper.selectCount(param);
	}

	@Override
	public PaginationResultVO<OrderLogisticsInfo> findListByPage(OrderLogisticsInfoQuery param) {
		int count = this.findCountByParam(param);
		int pageSize = param.getPageSize() == null ? PageSize.SIZE15.getSize() : param.getPageSize();

		SimplePage page = new SimplePage(param.getPageNo(), count, pageSize);
		param.setSimplePage(page);
		List<OrderLogisticsInfo> list = this.findListByParam(param);
		PaginationResultVO<OrderLogisticsInfo> result = new PaginationResultVO(count, page.getPageSize(), page.getPageNo(), page.getPageTotal(), list);
		return result;
	}

	@Override
	public Integer add(OrderLogisticsInfo bean) {
		return this.orderLogisticsInfoMapper.insert(bean);
	}

	@Override
	public Integer addBatch(List<OrderLogisticsInfo> listBean) {
		if (listBean == null || listBean.isEmpty()) {
			return 0;
		}
		return this.orderLogisticsInfoMapper.insertBatch(listBean);
	}

	@Override
	public Integer addOrUpdateBatch(List<OrderLogisticsInfo> listBean) {
		if (listBean == null || listBean.isEmpty()) {
			return 0;
		}
		return this.orderLogisticsInfoMapper.insertOrUpdateBatch(listBean);
	}

	@Override
	public Integer updateByParam(OrderLogisticsInfo bean, OrderLogisticsInfoQuery param) {
		StringTools.checkParam(param);
		return this.orderLogisticsInfoMapper.updateByParam(bean, param);
	}

	@Override
	public Integer deleteByParam(OrderLogisticsInfoQuery param) {
		StringTools.checkParam(param);
		return this.orderLogisticsInfoMapper.deleteByParam(param);
	}

	@Override
	public OrderLogisticsInfo getOrderLogisticsInfoByOrderId(String orderId) {
		return this.orderLogisticsInfoMapper.selectByOrderId(orderId);
	}

	@Override
	public Integer updateOrderLogisticsInfoByOrderId(OrderLogisticsInfo bean, String orderId) {
		return this.orderLogisticsInfoMapper.updateByOrderId(bean, orderId);
	}

	@Override
	public Integer deleteOrderLogisticsInfoByOrderId(String orderId) {
		return this.orderLogisticsInfoMapper.deleteByOrderId(orderId);
	}

	@Override
	public OrderLogisticsInfo getOrderLogisticsRecords(String userId, String orderId) {
		// 获得OrderLogisticsInfo
		OrderLogisticsInfo orderLogisticsInfo = this.getOrderLogisticsInfoByOrderId(orderId);
		if (orderLogisticsInfo == null) {
			throw new BusinessException("物流信息不存在");
		}
		if (!orderLogisticsInfo.getUserId().equals(userId) && userId != null){
			throw new BusinessException("物流信息不存在");
		}
		// 查询物流运输记录
		List<OrderLogisticsInfoRecord> recordList = new ArrayList<>();
		// 根据orderId查询，按recordId倒序排序
		OrderLogisticsInfoRecordQuery recordQuery = new OrderLogisticsInfoRecordQuery();
		recordQuery.setOrderId(orderId);
		recordQuery.setOrderBy(com.aishop.entity.query.SafeSort.of("record_id desc"));
		recordList = orderLogisticsInfoRecordService.findListByParam(recordQuery);
		orderLogisticsInfo.setRecordList(recordList);
		return orderLogisticsInfo;
	}

	@Override
	@Transactional(rollbackFor = Exception.class)
	public void delivery(OrderLogisticsInfo orderLogisticsInfo) {
		OrderLogisticsInfoQuery query = new OrderLogisticsInfoQuery();
		orderLogisticsInfo.setLogisticsStatus(LogisticsStatusEnum.IN_TRANSIT.getStatus());
		query.setOrderId(orderLogisticsInfo.getOrderId());
		Integer count = this.updateByParam(orderLogisticsInfo, query);
		if (count != 1) {
			throw new BusinessException("该订单已经发货过了");
		}
		// 修改订单状态为已发货
		OrderInfoQuery orderInfoQuery = new OrderInfoQuery();
		orderInfoQuery.setOrderId(orderLogisticsInfo.getOrderId());
		orderInfoQuery.setOrderStatus(OrderStatusEnum.PAID.getStatus());
		OrderInfo orderInfo = new OrderInfo();
		orderInfo.setOrderStatus(OrderStatusEnum.SHIPPED.getStatus());
		count = orderInfoService.updateByParam(orderInfo, orderInfoQuery);
		if (count != 1) {
			throw new BusinessException("该订单已经发货过了");
		}
		// 将发货信息插入order_logistics_info_record表
		OrderLogisticsInfoRecord record = new OrderLogisticsInfoRecord();
		record.setOrderId(orderLogisticsInfo.getOrderId());
		record.setRecordTime(new Date());
		record.setRecordAddress(orderLogisticsInfo.getSenderAddress());
		orderLogisticsInfoRecordService.add(record);
		PayOrderMessageDTO confirmDto = new PayOrderMessageDTO(orderLogisticsInfo.getOrderId());
		transactionalMqSender.sendAfterCommit(
				RabbitMQConfig.PAY_EXCHANGE,
				RabbitMQConfig.PAY_CONFIRM_DELAY_KEY,
				confirmDto,
				MqIdempotencyKeys.payConfirm(orderLogisticsInfo.getOrderId()),
				MessageReliabilityLevelEnum.STANDARD);
		OrderInfo shippedOrder = orderInfoService.getOrderInfoByOrderId(orderLogisticsInfo.getOrderId());
		if (shippedOrder != null && !StringTools.isEmpty(shippedOrder.getUserId())) {
			orderNotificationPublisher.send(shippedOrder.getUserId(), "订单已发货",
					"您的订单 " + orderLogisticsInfo.getOrderId() + " 已发货，物流单号："
							+ (orderLogisticsInfo.getLogisticsNo() == null ? "待更新" : orderLogisticsInfo.getLogisticsNo()),
					"logistics", orderLogisticsInfo.getOrderId());
		}
	}
}
