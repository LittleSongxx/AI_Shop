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
