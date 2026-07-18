package com.myshop.biz;

import java.util.List;

import com.myshop.entity.query.OrderLogisticsInfoRecordQuery;
import com.myshop.entity.po.OrderLogisticsInfoRecord;
import com.myshop.entity.vo.PaginationResultVO;

public interface OrderLogisticsInfoRecordService {

	List<OrderLogisticsInfoRecord> findListByParam(OrderLogisticsInfoRecordQuery param);

	Integer findCountByParam(OrderLogisticsInfoRecordQuery param);

	PaginationResultVO<OrderLogisticsInfoRecord> findListByPage(OrderLogisticsInfoRecordQuery param);

	Integer add(OrderLogisticsInfoRecord bean);

	Integer addBatch(List<OrderLogisticsInfoRecord> listBean);

	Integer addOrUpdateBatch(List<OrderLogisticsInfoRecord> listBean);

	Integer updateByParam(OrderLogisticsInfoRecord bean,OrderLogisticsInfoRecordQuery param);

	Integer deleteByParam(OrderLogisticsInfoRecordQuery param);

	OrderLogisticsInfoRecord getOrderLogisticsInfoRecordByRecordId(Integer recordId);

	Integer updateOrderLogisticsInfoRecordByRecordId(OrderLogisticsInfoRecord bean,Integer recordId);

	Integer deleteOrderLogisticsInfoRecordByRecordId(Integer recordId);

}
