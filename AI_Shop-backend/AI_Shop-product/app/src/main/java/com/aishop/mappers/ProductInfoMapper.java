package com.aishop.mappers;

import com.aishop.entity.po.ProductInfo;
import com.aishop.entity.po.ProductPropertyValue;
import com.aishop.entity.po.ProductSku;
import com.aishop.entity.query.ProductInfoQuery;
import org.apache.ibatis.annotations.Param;

import java.util.List;

public interface ProductInfoMapper<T,P> extends BaseMapper<T,P> {

	 Integer updateByProductId(@Param("bean") T t,@Param("productId") String productId);

	 Integer deleteByProductId(@Param("productId") String productId);

	 T selectByProductId(@Param("productId") String productId);

    Integer addProduct(@Param("productInfo") ProductInfo productInfo , @Param("productPropertyList") List<ProductPropertyValue> productPropertyList, @Param("productSkuList") List<ProductSku> productSkuList);

	Integer updateTotalSale(@Param("list") List<String> productIdList);

	Integer updateTotalSaleByCount(@Param("map") java.util.Map<String, Integer> productSaleMap);

	Integer increaseTotalSale(@Param("productId") String productId, @Param("qty") int qty);

	List<String> selectAllProductIds();

	Integer selectCountByCategoryUnion(@Param("query") ProductInfoQuery query);

	List<ProductInfo> selectListByCategoryUnion(@Param("query") ProductInfoQuery query);
}
