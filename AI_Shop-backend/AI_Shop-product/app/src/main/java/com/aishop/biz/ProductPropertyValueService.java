package com.aishop.biz;

import java.util.List;

import com.aishop.entity.query.ProductPropertyValueQuery;
import com.aishop.entity.po.ProductPropertyValue;
import com.aishop.entity.vo.PaginationResultVO;

public interface ProductPropertyValueService {

	List<ProductPropertyValue> findListByParam(ProductPropertyValueQuery param);

	Integer findCountByParam(ProductPropertyValueQuery param);

	PaginationResultVO<ProductPropertyValue> findListByPage(ProductPropertyValueQuery param);

	Integer add(ProductPropertyValue bean);

	Integer addBatch(List<ProductPropertyValue> listBean);

	Integer addOrUpdateBatch(List<ProductPropertyValue> listBean);

	Integer updateByParam(ProductPropertyValue bean,ProductPropertyValueQuery param);

	Integer deleteByParam(ProductPropertyValueQuery param);

	ProductPropertyValue getProductPropertyValueByProductIdAndPropertyValueId(String productId,String propertyValueId);

	Integer updateProductPropertyValueByProductIdAndPropertyValueId(ProductPropertyValue bean,String productId,String propertyValueId);

	Integer deleteProductPropertyValueByProductIdAndPropertyValueId(String productId,String propertyValueId);

}
