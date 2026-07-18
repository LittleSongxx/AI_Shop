package com.myshop.mappers;

import com.myshop.domain.SkuStock;
import org.apache.ibatis.annotations.Param;

public interface SkuStockMapper {

    SkuStock selectByKey(@Param("productId") String productId,
                         @Param("propertyValueIdHash") String propertyValueIdHash);

    SkuStock selectByKeyForUpdate(@Param("productId") String productId,
                                  @Param("propertyValueIdHash") String propertyValueIdHash);

    int changeStock(@Param("productId") String productId,
                    @Param("propertyValueIdHash") String propertyValueIdHash,
                    @Param("changeAmount") Integer changeAmount);

    Integer selectTotalStockByProductId(@Param("productId") String productId);

    int upsert(@Param("productId") String productId,
               @Param("propertyValueIdHash") String propertyValueIdHash,
               @Param("stock") Integer stock);

    Integer countLessThan(@Param("threshold") int threshold);

    java.util.List<SkuStock> selectLessThan(@Param("threshold") int threshold,
                                            @Param("offset") int offset,
                                            @Param("limit") int limit);
}
