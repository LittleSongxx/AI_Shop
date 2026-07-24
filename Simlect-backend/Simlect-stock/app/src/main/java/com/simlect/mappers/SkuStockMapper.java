package com.simlect.mappers;

import com.simlect.domain.SkuStock;
import com.simlect.api.vo.ProductTotalStockVO;
import org.apache.ibatis.annotations.Param;

import java.util.List;

public interface SkuStockMapper {

    SkuStock selectByKey(@Param("productId") String productId,
                         @Param("propertyValueIdHash") String propertyValueIdHash);

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
