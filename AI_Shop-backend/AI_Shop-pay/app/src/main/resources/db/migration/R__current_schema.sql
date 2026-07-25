-- Current schema owned by the pay service.
create table if not exists pay_trade_record
(
    trade_id         varchar(33)          not null primary key,
    order_id         varchar(32)          not null,
    user_id          varchar(15)          not null,
    pay_order_id     varchar(32)          not null comment '与 order_info.pay_order_id 一致',
    channel_order_id varchar(50)          null comment '与 order_info.channel_order_Id 一致',
    pay_channel      varchar(10)          null,
    pay_amount       decimal(10, 2)       not null,
    trade_status     tinyint(1) default 0 not null comment '0待支付 1成功 2关闭 3退款',
    pay_time         datetime             null,
    create_time      datetime             not null,
    constraint uk_pay_trade_pay_order unique (pay_order_id)
) comment '支付流水' collate = utf8mb4_general_ci row_format = DYNAMIC;
SET @sql = IF(
    EXISTS (
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'pay_trade_record'
          AND index_name = 'uk_pay_trade_pay_order'
    ),
    'SELECT 1',
    'ALTER TABLE pay_trade_record ADD CONSTRAINT uk_pay_trade_pay_order UNIQUE (pay_order_id)'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
