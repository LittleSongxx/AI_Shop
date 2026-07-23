package com.simlect.mappers;

import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

public interface StockChangeRecordMapper {

    @Insert("""
            INSERT IGNORE INTO stock_change_record
                (business_key, change_type, product_id, property_value_id_hash, change_amount, created_at)
            VALUES
                (#{businessKey}, #{changeType}, #{productId}, #{propertyValueIdHash}, #{changeAmount}, NOW())
            """)
    int insertIgnore(@Param("businessKey") String businessKey,
                     @Param("changeType") String changeType,
                     @Param("productId") String productId,
                     @Param("propertyValueIdHash") String propertyValueIdHash,
                     @Param("changeAmount") Integer changeAmount);

    @Select("SELECT COUNT(1) FROM stock_change_record WHERE business_key = #{businessKey}")
    int exists(@Param("businessKey") String businessKey);
}
