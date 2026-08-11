package com.aishop.mappers;

import org.apache.ibatis.annotations.Param;

public interface ImageModerationRecordMapper<T, P> extends BaseMapper<T, P> {

    T selectByRecordId(@Param("recordId") Integer recordId);

    T selectByAssetId(@Param("assetId") String assetId);

    Integer updateByRecordId(@Param("bean") T t, @Param("recordId") Integer recordId);

    Integer updateByRecordIdIfPending(@Param("bean") T t, @Param("recordId") Integer recordId);

    java.util.List<T> selectExpiredAgentAssets(@Param("limit") Integer limit);

    Integer markAssetPurged(@Param("recordId") Integer recordId,
                            @Param("purgedAt") java.util.Date purgedAt);

    Integer retainAsset(@Param("assetId") String assetId,
                        @Param("retentionClass") String retentionClass);

    Integer deleteByRecordId(@Param("recordId") Integer recordId);
}
