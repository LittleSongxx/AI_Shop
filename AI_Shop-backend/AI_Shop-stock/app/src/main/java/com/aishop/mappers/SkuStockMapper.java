package com.aishop.mappers;

import com.aishop.domain.SkuStock;
import com.aishop.api.dto.SkuStockQueryDTO;
import com.aishop.api.vo.ProductTotalStockVO;
import org.apache.ibatis.annotations.Param;

import java.util.List;

public interface SkuStockMapper {

    SkuStock selectByKey(@Param("productId") String productId,
                         @Param("propertyValueIdHash") String propertyValueIdHash);

    List<SkuStock> selectByKeys(@Param("items") List<SkuStockQueryDTO> items);

    SkuStock selectByKeyForUpdate(@Param("productId") String productId,
                                  @Param("propertyValueIdHash") String propertyValueIdHash);

    int changeStock(@Param("productId") String productId,
                    @Param("propertyValueIdHash") String propertyValueIdHash,
                    @Param("changeAmount") Integer changeAmount);

    Integer selectTotalStockByProductId(@Param("productId") String productId);

    List<ProductTotalStockVO> selectTotalStockByProductIds(@Param("productIds") List<String> productIds);

    int upsert(@Param("productId") String productId,
               @Param("propertyValueIdHash") String propertyValueIdHash,
               @Param("stock") Integer stock);

    Integer countLessThan(@Param("threshold") int threshold);

    java.util.List<SkuStock> selectLessThan(@Param("threshold") int threshold,
                                            @Param("offset") int offset,
                                            @Param("limit") int limit);
}
