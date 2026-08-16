-- Current schema owned by the product service.

create table if not exists product_info
(
    product_id    varchar(15)          not null comment '商品ID' primary key,
    product_name  varchar(200)         null comment '商品名称',
    product_desc  text                 null comment '商品描述',
    cover         varchar(500)         null comment '封面',
    create_time   datetime             null comment '创建时间',
    category_id   varchar(10)          null comment '分类ID',
    p_category_id varchar(10)          null comment '分类父ID',
    status        tinyint(1) default 0 null comment '-1:已删除 0:下架  1:上架',
    min_price     decimal(10, 2)       null comment '最低价格',
    max_price     decimal(10, 2)       null comment '最高价格',
    total_sale    int        default 0 null comment '销量',
    commend_type  tinyint(1) default 0 null comment '0:未推荐 1:已经推荐'
) comment '商品信息' collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists product_property_value
(
    product_id        varchar(15)  not null comment '商品ID',
    property_id       varchar(10)  null comment '属性ID',
    property_name     varchar(30)  null comment '属性名称',
    property_sort     int          null comment '属性排序',
    cover_type        tinyint(1)   null comment '0:无需传封面 1:需传封面',
    property_value_id varchar(15)  not null,
    property_cover    varchar(60)  null comment '属性封面',
    property_value    varchar(100) null comment '属性值',
    property_remark   varchar(100) null comment '备注',
    sort              int          null comment '属性值排序',
    primary key (product_id, property_value_id)
) collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists product_sku
(
    product_id             varchar(15)    not null comment '商品ID',
    property_value_id_hash varchar(32)    not null comment '属性值id组hash',
    property_value_ids     varchar(500)   null comment '属性值id组',
    price                  decimal(10, 2) null comment '价格',
    sort                   int            null comment '排序',
    primary key (product_id, property_value_id_hash)
) comment 'SKU（库存已迁至 aishop_stock.sku_stock）' collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists sys_category
(
    category_id   varchar(5)             not null primary key,
    category_name varchar(100)           null,
    p_category_id varchar(5) default '0' null,
    sort          int        default 0   null
) collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists sys_product_property
(
    property_id   varchar(10)            not null comment '属性ID' primary key,
    property_name varchar(30)            null comment '属性名称',
    p_category_id varchar(5)             null comment '一级分类',
    category_id   varchar(5) default '0' null comment '二级分类',
    property_sort int                    null comment '排序',
    cover_type    tinyint(1)             null comment '0:无需传封面 1:需传封面'
) collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists local_message_outbox
(
    id                bigint auto_increment comment '主键' primary key,
    idempotency_key   varchar(128)                    not null comment '发送幂等键',
    exchange_name     varchar(64)                     not null comment '交换机',
    routing_key       varchar(64)                     not null comment '路由键',
    payload_json      mediumtext                      not null comment '消息体 JSON',
    reliability_level varchar(16) default 'STANDARD'  not null comment 'HIGH/STANDARD',
    status            tinyint     default 0           not null comment '0待发送 1发送中 2已发送 3失败 4重试耗尽',
    retry_count       int         default 0           not null comment '重试次数',
    error_message     varchar(512)                    null comment '最近失败原因',
    lease_owner       varchar(64)                     null comment '当前投递实例',
    lease_until       datetime                        null comment '发送租约截止时间',
    next_retry_time   datetime                        null comment '下次可重试时间',
    create_time       datetime                        not null comment '创建时间',
    update_time       datetime                        null comment '更新时间',
    sent_time         datetime                        null comment '成功发送时间',
    constraint uk_outbox_idempotency unique (idempotency_key),
    key idx_outbox_status_ctime (status, create_time),
    key idx_outbox_dispatch (status, next_retry_time, lease_until, id)
) comment '本地消息 Outbox' charset = utf8mb4;

create table if not exists mq_compensation_log
(
    log_id            int auto_increment comment '日志ID' primary key,
    idempotency_key   varchar(128)               not null comment '幂等键',
    exchange          varchar(64)                not null comment '交换机',
    routing_key       varchar(64)                not null comment '路由键',
    biz_scene         varchar(32)                null comment '业务场景',
    payload_json      mediumtext                 null comment '消息体 JSON',
    reliability_level varchar(16) default 'HIGH' not null comment '发送级别',
    error_message     varchar(512)               null comment '失败原因',
    retry_count       int         default 0      not null comment '重放次数',
    status            int         default 0      not null comment '0待处理 1处理中 2已重放成功 3重放失败 4已忽略',
    create_time       datetime                   not null comment '创建时间',
    update_time       datetime                   null comment '更新时间',
    handle_time       datetime                   null comment '运维处理时间',
    handle_remark     varchar(512)               null comment '处理备注',
    constraint uk_idempotency_key unique (idempotency_key)
) comment 'MQ补偿审查日志' charset = utf8mb4;

-- Add Outbox lease columns for databases created before the reliable dispatcher.
SET @sql = IF(
    EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'local_message_outbox'
          AND column_name = 'lease_owner'
    ),
    'SELECT 1',
    'ALTER TABLE local_message_outbox ADD COLUMN lease_owner varchar(64) NULL COMMENT ''current dispatcher instance'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'local_message_outbox'
          AND column_name = 'lease_until'
    ),
    'SELECT 1',
    'ALTER TABLE local_message_outbox ADD COLUMN lease_until datetime NULL COMMENT ''sending lease deadline'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'local_message_outbox'
          AND column_name = 'next_retry_time'
    ),
    'SELECT 1',
    'ALTER TABLE local_message_outbox ADD COLUMN next_retry_time datetime NULL COMMENT ''next eligible retry time'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'local_message_outbox'
          AND index_name = 'idx_outbox_dispatch'
    ),
    'SELECT 1',
    'ALTER TABLE local_message_outbox ADD INDEX idx_outbox_dispatch (status, next_retry_time, lease_until, id)'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
