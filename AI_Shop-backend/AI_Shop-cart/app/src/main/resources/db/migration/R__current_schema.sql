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
