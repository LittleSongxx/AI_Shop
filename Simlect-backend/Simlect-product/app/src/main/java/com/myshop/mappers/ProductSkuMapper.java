package com.myshop.mappers;

import com.myshop.entity.po.ProductItem;
import com.myshop.entity.po.ProductPropertyValue;
import com.myshop.entity.po.ProductSku;
import com.myshop.entity.query.ProductSkuQuery;
import com.myshop.entity.vo.ProductSkuListVO;
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

	ProductSku selectListByProductId(String productId);

	ProductSku selectByProductId(String productId);

	void updateBatch(@Param("productId") String productId,@Param("updateList")  List<ProductSku> updateList);

	void deleteBatch(@Param("productId") String productId,@Param("deleteList")  List<ProductSku> deleteList);

	void updateBySkuId(@Param("productSku")ProductSku productSku, @Param("skuId") String skuId);

	Integer updateStockBatch(@Param("orderList") List<ProductItem> orderList);

    List<ProductSkuListVO> selectList4ListVO(ProductSkuQuery query);
}
