-- Current schema owned by the cart service.
create table if not exists product_cart
(
    cart_id                varchar(15)  not null comment '购物车ID' primary key,
    user_id                varchar(15)  null comment '用户ID',
    product_id             varchar(15)  not null comment '商品ID',
    property_value_ids     varchar(500) null comment '属性值id组',
    property_value_id_hash varchar(32)  null comment '属性值id组hash',
    buy_count              int          null comment '数量',
    add_price              decimal(10, 2) null comment '加入购物车时的单价',
    ai_request_id          varchar(128) null comment '已验证的推荐请求ID',
    ai_position            smallint unsigned null comment '推荐位次（从1开始）',
    ai_source              varchar(40) null comment '服务端推荐来源',
    ai_attributed_at       datetime(3) null comment '已验证点击时间',
    last_update_time       datetime     null comment '更新时间',
    create_time            datetime     null comment '创建时间',
    constraint idx_key unique (product_id, property_value_id_hash, user_id)
) comment '购物车' collate = utf8mb4_general_ci row_format = DYNAMIC;
SET @sql = IF(
    EXISTS (
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'product_cart'
          AND index_name = 'idx_cart_user_time'
    ),
    'SELECT 1',
    'ALTER TABLE product_cart ADD INDEX idx_cart_user_time (user_id, last_update_time)'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- The cart mutation and its commerce-outcome event commit atomically.
create table if not exists local_message_outbox
(
    id                bigint auto_increment comment '主键' primary key,
    idempotency_key   varchar(128)                   not null comment '发送幂等键',
    exchange_name     varchar(64)                    not null comment '交换机',
    routing_key       varchar(64)                    not null comment '路由键',
    payload_json      mediumtext                     not null comment '消息体 JSON',
    reliability_level varchar(16) default 'STANDARD' not null comment 'HIGH/STANDARD',
    status            tinyint     default 0          not null comment '0待发送 1发送中 2已发送 3失败 4重试耗尽',
    retry_count       int         default 0          not null comment '重试次数',
    error_message     varchar(512)                   null comment '最近失败原因',
    lease_owner       varchar(64)                    null comment '当前投递实例',
    lease_until       datetime                       null comment '发送租约截止时间',
    next_retry_time   datetime                       null comment '下次可重试时间',
    create_time       datetime                       not null comment '创建时间',
    update_time       datetime                       null comment '更新时间',
    sent_time         datetime                       null comment '成功发送时间',
    constraint uk_outbox_idempotency unique (idempotency_key),
    key idx_outbox_status_ctime (status, create_time),
    key idx_outbox_dispatch (status, next_retry_time, lease_until, id)
) comment '本地消息 Outbox（事务后可靠投递）' charset = utf8mb4;

SET @sql = IF(
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'product_cart'
              AND column_name = 'ai_request_id'),
    'SELECT 1',
    'ALTER TABLE product_cart ADD COLUMN ai_request_id varchar(128) NULL COMMENT ''validated recommendation request ID'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'product_cart'
              AND column_name = 'ai_position'),
    'SELECT 1',
    'ALTER TABLE product_cart ADD COLUMN ai_position smallint unsigned NULL COMMENT ''one-based recommendation position'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'product_cart'
              AND column_name = 'ai_source'),
    'SELECT 1',
    'ALTER TABLE product_cart ADD COLUMN ai_source varchar(40) NULL COMMENT ''server-owned recommendation source'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'product_cart'
              AND column_name = 'ai_attributed_at'),
    'SELECT 1',
    'ALTER TABLE product_cart ADD COLUMN ai_attributed_at datetime(3) NULL COMMENT ''validated click time'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
