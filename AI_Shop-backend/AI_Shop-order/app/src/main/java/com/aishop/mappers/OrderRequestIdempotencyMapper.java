package com.aishop.mappers;

import com.aishop.entity.po.OrderRequestIdempotency;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Options;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

public interface OrderRequestIdempotencyMapper {

    @Insert("""
            insert ignore into order_request_idempotency
                (user_id, command_type, idempotency_key, request_hash, status, create_time, update_time)
            values
                (#{userId}, #{commandType}, #{idempotencyKey}, #{requestHash},
                 'PROCESSING', current_timestamp, current_timestamp)
            """)
    @Options(useGeneratedKeys = true, keyProperty = "id")
    int insertProcessing(OrderRequestIdempotency record);

    @Select("""
            select id, user_id, command_type, idempotency_key, request_hash,
                   status, response_json, create_time, update_time
            from order_request_idempotency
            where user_id = #{userId}
              and command_type = #{commandType}
              and idempotency_key = #{idempotencyKey}
            for update
            """)
    OrderRequestIdempotency selectForUpdate(
            @Param("userId") String userId,
            @Param("commandType") String commandType,
            @Param("idempotencyKey") String idempotencyKey);

    @Select("""
            select id, user_id, command_type, idempotency_key, request_hash,
                   status, response_json, create_time, update_time
            from order_request_idempotency
            where user_id = #{userId}
              and command_type = #{commandType}
              and idempotency_key = #{idempotencyKey}
            """)
    OrderRequestIdempotency select(
            @Param("userId") String userId,
            @Param("commandType") String commandType,
            @Param("idempotencyKey") String idempotencyKey);

    @Update("""
            update order_request_idempotency
            set status = 'COMPLETED',
                response_json = #{responseJson},
                update_time = current_timestamp
            where user_id = #{userId}
              and command_type = #{commandType}
              and idempotency_key = #{idempotencyKey}
              and status = 'PROCESSING'
            """)
    int markCompleted(
            @Param("userId") String userId,
            @Param("commandType") String commandType,
            @Param("idempotencyKey") String idempotencyKey,
            @Param("responseJson") String responseJson);

    @Update("""
            update order_request_idempotency
            set status = 'FAILED',
                response_json = #{responseJson},
                update_time = current_timestamp
            where user_id = #{userId}
              and command_type = #{commandType}
              and idempotency_key = #{idempotencyKey}
              and status = 'PROCESSING'
            """)
    int markFailed(
            @Param("userId") String userId,
            @Param("commandType") String commandType,
            @Param("idempotencyKey") String idempotencyKey,
            @Param("responseJson") String responseJson);

    @Update("""
            update order_request_idempotency
            set status = 'COMPLETED',
                response_json = #{responseJson},
                update_time = current_timestamp
            where user_id = #{userId}
              and command_type = #{commandType}
              and idempotency_key = #{idempotencyKey}
              and status in ('PROCESSING', 'FAILED')
            """)
    int markReconciled(
            @Param("userId") String userId,
            @Param("commandType") String commandType,
            @Param("idempotencyKey") String idempotencyKey,
            @Param("responseJson") String responseJson);
}
