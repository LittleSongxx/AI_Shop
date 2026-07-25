-- Current schema owned by the order service.
create table if not exists order_info
(
    order_id         varchar(32)                 not null comment '订单ID' primary key,
    amount           decimal(10, 2)              null comment '金额',
    goods_amount     decimal(10, 2)              null comment '商品原价合计（未优惠）',
    discount_amount  decimal(10, 2) default 0.00 not null comment '优惠抵扣总额（券+活动等）',
    coupon_discount  decimal(10, 2) default 0.00 not null comment '优惠券抵扣金额',
    user_coupon_id   varchar(32)                 null comment '使用的用户券ID user_coupon.user_coupon_id',
    coupon_id        varchar(20)                 null comment '券模板ID discount_coupon.coupon_id',
    user_id          varchar(15)                 null comment '用户ID',
    order_time       datetime                    null comment '订单创建时间',
    order_status     tinyint(1)                  null comment '-1已删除 0:待付款 1:已付款,待发货  2:已发货  3:已完成 4:已取消 5:已关闭 6:已退款 7:部分退款',
    pay_channel      varchar(10)                 null comment '支付通道',
    pay_scene        varchar(20)                 null comment '支付场景',
    pay_order_id     varchar(32)                 null comment '支付订单号',
    channel_order_Id varchar(50)                 null comment '通道ID',
    comment_status   tinyint        default 0    null comment '评价状态 0:未评价  1:已评价  2:已追评',
    subject          varchar(200)                null comment '订单标题'
) comment '订单信息' collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists order_item
(
    order_item_id          varchar(40)    not null comment '订单明细ID' primary key,
    order_id               varchar(32)    not null comment '订单ID',
    cover                  varchar(100)   null comment '封面',
    product_id             varchar(15)    not null comment '商品ID',
    product_name           varchar(200)   null comment '商品名称',
    property_value_id_hash varchar(32)    not null comment '属性值id组hash',
    property_info          varchar(150)   null comment '属性信息',
    item_amount            decimal(10, 2) null comment '价格',
    buy_count              int            null comment '数量',
    order_item_status      tinyint(1)     null comment '状态 1:正常 0:已退款',
    remark                 varchar(300)   null comment '备注',
    refund_order_id        varchar(32)    null comment '退款订单号'
) comment '订单明细表' collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists refund_request
(
    refund_request_id       varchar(32)                         not null primary key,
    refund_order_no         varchar(32)                         not null,
    source_pay_order_id     varchar(32)                         not null,
    order_id                varchar(32)                         not null,
    order_item_id           varchar(40)                         not null,
    user_id                 varchar(15)                         not null,
    product_id              varchar(15)                         not null,
    property_value_id_hash  varchar(32)                         not null,
    buy_count               int                                 not null,
    refund_amount           decimal(10, 2)                      not null,
    pay_channel             varchar(20)                         null,
    status                  varchar(32)                         not null comment 'PENDING_PAYMENT/PAYMENT_CONFIRMED/STOCK_PENDING/COMPLETED/MANUAL_REVIEW',
    retry_count             int       default 0                 not null,
    next_retry_time         datetime                            null,
    last_error              varchar(512)                        null,
    created_at              datetime  default current_timestamp not null,
    updated_at              datetime  default current_timestamp not null on update current_timestamp,
    payment_confirmed_at    datetime                            null,
    completed_at            datetime                            null,
    constraint uk_refund_order_no unique (refund_order_no),
    constraint uk_refund_order_item unique (order_item_id),
    key idx_refund_retry (status, next_retry_time)
) comment '持久化退款 Saga' charset = utf8mb4;

create table if not exists order_comment
(
    order_id          varchar(32)       not null comment '订单ID' primary key,
    product_id        varchar(15)       not null comment '商品ID',
    comment_content   varchar(300)      null comment '评价内容',
    comment_time      datetime          null comment '评价时间',
    comment_images    varchar(300)      null comment '评价图片',
    star              int               null comment '评价星级',
    comment_biz_reply varchar(255)      null comment '商家回复',
    recomment_content varchar(300)      null comment '追评',
    recomment_time    datetime          null comment '追评时间',
    recomment_images  varchar(300)      null comment '追评图片',
    user_id           varchar(15)       null comment '用户ID',
    property_info     varchar(150)      null comment '属性信息',
    status            tinyint default 0 null comment '0:正常 1:已删除'
) collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists order_coupon_rel
(
    id              bigint auto_increment primary key,
    order_id        varchar(32)    not null,
    user_coupon_id  varchar(32)    not null,
    coupon_id       varchar(20)    not null,
    discount_amount decimal(10, 2) not null,
    create_time     datetime       not null,
    constraint uk_order_user_coupon unique (order_id, user_coupon_id)
) comment '订单优惠券核销明细' collate = utf8mb4_general_ci;

create table if not exists order_request_idempotency
(
    id              bigint auto_increment primary key,
    user_id         varchar(15)                         not null,
    command_type    varchar(32)                         not null,
    idempotency_key varchar(64)                         not null,
    request_hash    char(64)                            not null,
    status          varchar(16) default 'PROCESSING'    not null,
    response_json   mediumtext                          null,
    create_time     datetime    default current_timestamp not null,
    update_time     datetime    default current_timestamp not null on update current_timestamp,
    constraint uk_order_request_idempotency unique (user_id, command_type, idempotency_key),
    key idx_order_request_status (status, update_time)
) comment '订单命令幂等记录' collate = utf8mb4_general_ci;

create table if not exists comment_report
(
    report_id        int auto_increment comment '举报ID' primary key,
    order_id         varchar(64)       not null comment '被举报评论所属订单ID',
    product_id       varchar(64)       null comment '商品ID',
    reporter_user_id varchar(64)       null comment '举报人用户ID',
    reason           varchar(50)       not null comment '举报理由',
    detail           varchar(500)      null comment '补充说明',
    comment_snapshot varchar(1000)     null comment '举报时评论内容快照',
    status           tinyint default 0 not null comment '0:待处理 1:已处理 2:已驳回',
    report_time      datetime          null comment '举报时间',
    handle_time      datetime          null comment '处理时间',
    handle_remark    varchar(500)      null comment '处理备注'
) comment '评论举报';

-- Outbox / 补偿（与订单业务同库）
create table if not exists local_message_outbox
(
    id                bigint auto_increment comment '主键' primary key,
    idempotency_key   varchar(128)                   not null comment '发送幂等键',
    exchange_name     varchar(64)                    not null comment '交换机',
    routing_key       varchar(64)                    not null comment '路由键',
    payload_json      mediumtext                     not null comment '消息体 JSON',
    reliability_level varchar(16) default 'STANDARD' not null comment 'HIGH/STANDARD',
    status            tinyint     default 0          not null comment '0待发送 1发送中 2已发送 3失败',
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
) comment 'MQ补偿审查日志（订单库）' charset = utf8mb4;

-- 物流信息（订单域，同库）
create table if not exists order_logistics_info
(
    order_id          varchar(32)       not null comment '订单编号' primary key,
    user_id           varchar(15)       null comment '用户ID',
    logistics_no      varchar(128)      null comment '物流单号',
    logistics_company varchar(100)      null comment '物流公司',
    sender_name       varchar(100)      null comment '发货人姓名',
    sender_phone      varchar(20)       null comment '发货人电话',
    sender_address    varchar(500)      null comment '发货地址',
    receiver_name     varchar(100)      null comment '收件人姓名',
    receiver_phone    varchar(20)       null comment '收件人电话',
    receiver_address  varchar(500)      null comment '收件地址',
    logistics_status  tinyint default 0 null comment '物流状态：0待发货 1运输中 2已送达 3订单取消'
) comment '物流信息表' collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists order_logistics_info_record
(
    record_id      int auto_increment comment '记录ID' primary key,
    order_id       varchar(32)  not null comment '订单ID',
    record_time    datetime     null comment '记录时间',
    record_address varchar(150) null comment '记录地址'
) collate = utf8mb4_general_ci row_format = DYNAMIC;
-- Outbox lease fields
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
