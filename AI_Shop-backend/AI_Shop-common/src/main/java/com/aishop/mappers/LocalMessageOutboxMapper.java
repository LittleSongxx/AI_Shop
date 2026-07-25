package com.aishop.mappers;

import com.aishop.entity.po.LocalMessageOutbox;
import org.apache.ibatis.annotations.Param;

import java.util.Date;
import java.util.List;

public interface LocalMessageOutboxMapper {

    Integer insert(LocalMessageOutbox row);

    LocalMessageOutbox selectById(@Param("id") Long id);

    LocalMessageOutbox selectByIdempotencyKey(@Param("idempotencyKey") String idempotencyKey);

    Integer claimForDispatch(@Param("id") Long id,
                             @Param("pendingStatus") Integer pendingStatus,
                             @Param("failedStatus") Integer failedStatus,
                             @Param("sendingStatus") Integer sendingStatus,
                             @Param("leaseOwner") String leaseOwner,
                             @Param("leaseUntil") Date leaseUntil,
                             @Param("now") Date now);

    Integer markSent(@Param("id") Long id,
                     @Param("sendingStatus") Integer sendingStatus,
                     @Param("sentStatus") Integer sentStatus,
                     @Param("leaseOwner") String leaseOwner,
                     @Param("sentTime") Date sentTime);

    Integer markFailed(@Param("id") Long id,
                       @Param("sendingStatus") Integer sendingStatus,
                       @Param("failedStatus") Integer failedStatus,
                       @Param("leaseOwner") String leaseOwner,
                       @Param("errorMessage") String errorMessage,
                       @Param("nextRetryTime") Date nextRetryTime);

    List<LocalMessageOutbox> selectDispatchBatch(@Param("pendingStatus") Integer pendingStatus,
                                                 @Param("failedStatus") Integer failedStatus,
                                                 @Param("sendingStatus") Integer sendingStatus,
                                                 @Param("beforeTime") Date beforeTime,
                                                 @Param("now") Date now,
                                                 @Param("limit") int limit);
}
