package com.myshop.biz;

import java.util.List;

import com.myshop.entity.query.ProductSkuQuery;
import com.myshop.entity.po.ProductSku;
import com.myshop.entity.vo.PaginationResultVO;
import com.myshop.entity.vo.ProductSkuListVO;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;

public interface ProductSkuService {

	List<ProductSku> findListByParam(ProductSkuQuery param);

	Integer findCountByParam(ProductSkuQuery param);

	PaginationResultVO<ProductSku> findListByPage(ProductSkuQuery param);

	Integer add(ProductSku bean);

	Integer addBatch(List<ProductSku> listBean);

	Integer addOrUpdateBatch(List<ProductSku> listBean);

	Integer updateByParam(ProductSku bean,ProductSkuQuery param);

	Integer deleteByParam(ProductSkuQuery param);

	ProductSku getProductSkuByProductIdAndPropertyValueIdHash(String productId,String propertyValueIdHash);

	Integer updateProductSkuByProductIdAndPropertyValueIdHash(ProductSku bean,String productId,String propertyValueIdHash);

	Integer deleteProductSkuByProductIdAndPropertyValueIdHash(String productId,String propertyValueIdHash);

    void updateStock(@NotEmpty String productId, @NotEmpty String propertyValueIdHash, @NotNull Integer changeStock);

    PaginationResultVO<ProductSkuListVO> findListByPage4ListVO(ProductSkuQuery query);

    PaginationResultVO<ProductSkuListVO> lessStockSkuPage(Integer pageNo, Integer pageSize, Integer threshold);
}
