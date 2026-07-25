package com.aishop.mappers;

import com.aishop.entity.po.ProductPropertyValue;
import com.aishop.entity.po.ProductSku;
import com.aishop.entity.query.ProductSkuQuery;
import com.aishop.api.vo.ProductSkuListVO;
import com.aishop.entity.vo.ProductSkuCountVO;
import org.apache.ibatis.annotations.Param;

import java.util.List;

public interface ProductSkuMapper<T,P> extends BaseMapper<T,P> {

	 Integer updateByProductIdAndPropertyValueIdHash(@Param("bean") T t,@Param("productId") String productId,@Param("propertyValueIdHash") String propertyValueIdHash);

	 Integer deleteByProductIdAndPropertyValueIdHash(@Param("productId") String productId,@Param("propertyValueIdHash") String propertyValueIdHash);

	 T selectByProductIdAndPropertyValueIdHash(@Param("productId") String productId,@Param("propertyValueIdHash") String propertyValueIdHash);

	T selectByProductIdAndPropertyValueIdHashForUpdate(@Param("productId") String productId,
			@Param("propertyValueIdHash") String propertyValueIdHash);

    Integer selectTotalStockByProductId(@Param("productId") String productId);

	Integer selectCountByProductId(String productId);

	List<ProductSkuCountVO> selectCountByProductIds(@Param("productIds") List<String> productIds);

	ProductSku selectListByProductId(String productId);

	ProductSku selectByProductId(String productId);

	void updateBatch(@Param("productId") String productId,@Param("updateList")  List<ProductSku> updateList);

	void deleteBatch(@Param("productId") String productId,@Param("deleteList")  List<ProductSku> deleteList);

	void updateBySkuId(@Param("productSku")ProductSku productSku, @Param("skuId") String skuId);

    List<ProductSkuListVO> selectList4ListVO(ProductSkuQuery query);
}
