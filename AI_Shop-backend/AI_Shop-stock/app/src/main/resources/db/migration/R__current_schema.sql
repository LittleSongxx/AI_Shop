-- Current schema owned by the stock service.
CREATE TABLE IF NOT EXISTS sku_stock (
    product_id             varchar(15)  NOT NULL COMMENT '商品ID',
    property_value_id_hash varchar(32)  NOT NULL COMMENT '属性值id组hash',
    stock                  int          NOT NULL COMMENT '库存',
    PRIMARY KEY (product_id, property_value_id_hash)
) COMMENT 'SKU库存（从 product_sku.stock 迁出）' COLLATE = utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS stock_change_record (
    business_key            varchar(96)                         NOT NULL PRIMARY KEY,
    change_type             varchar(32)                         NOT NULL,
    product_id              varchar(15)                         NOT NULL,
    property_value_id_hash  varchar(32)                         NOT NULL,
    change_amount           int                                 NOT NULL,
    created_at              datetime default current_timestamp  NOT NULL,
    KEY idx_stock_change_sku (product_id, property_value_id_hash)
) COMMENT '库存幂等变更记录' CHARSET = utf8mb4;

-- MQ 补偿审查日志：退款库存恢复的 MQ 消费失败时由 RemoteCompensateRecorder 写入，
-- MqCompensationAutoReplayTask 周期扫描并重放；本表结构与其他库保持一致。
CREATE TABLE IF NOT EXISTS mq_compensation_log
(
    log_id            int auto_increment                  NOT NULL COMMENT '日志ID' PRIMARY KEY,
    idempotency_key   varchar(128)                        NOT NULL COMMENT '幂等键',
    exchange          varchar(64)                         NOT NULL COMMENT '交换机',
    routing_key       varchar(64)                         NOT NULL COMMENT '路由键',
    biz_scene         varchar(32)                         NULL     COMMENT '业务场景',
    payload_json      mediumtext                          NULL     COMMENT '消息体 JSON',
    reliability_level varchar(16) DEFAULT 'HIGH'          NOT NULL COMMENT '发送级别',
    error_message     varchar(512)                        NULL     COMMENT '失败原因',
    retry_count       int         DEFAULT 0               NOT NULL COMMENT '重放次数',
    status            int         DEFAULT 0               NOT NULL COMMENT '0待处理 1处理中 2已重放成功 3重放失败 4已忽略',
    create_time       datetime                            NOT NULL COMMENT '创建时间',
    update_time       datetime                            NULL     COMMENT '更新时间',
    handle_time       datetime                            NULL     COMMENT '运维处理时间',
    handle_remark     varchar(512)                        NULL     COMMENT '处理备注',
    CONSTRAINT uk_idempotency_key UNIQUE (idempotency_key)
) COMMENT 'MQ补偿审查日志（stock库）' CHARSET = utf8mb4;
