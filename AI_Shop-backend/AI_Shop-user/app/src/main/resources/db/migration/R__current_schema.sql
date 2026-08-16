-- Current schema owned by the user service.

create table if not exists admin_account
(
    admin_id             bigint auto_increment primary key,
    account              varchar(100)                         not null,
    password_hash        varchar(100)                         not null,
    display_name         varchar(100)                         null,
    status               tinyint      default 1              not null comment '0 disabled, 1 active',
    session_version      bigint       default 1              not null,
    migrated_from_config tinyint      default 0              not null,
    last_login_at        datetime(3)                          null,
    created_at           datetime(3) default current_timestamp(3) not null,
    updated_at           datetime(3) default current_timestamp(3) not null
        on update current_timestamp(3),
    constraint uk_admin_account unique (account)
) comment 'database-backed administrator identity' charset = utf8mb4;

create table if not exists admin_role
(
    role_id      bigint auto_increment primary key,
    role_code    varchar(64)  not null,
    role_name    varchar(100) not null,
    description  varchar(255) null,
    constraint uk_admin_role_code unique (role_code)
) comment 'administrator role' charset = utf8mb4;

create table if not exists admin_permission
(
    permission_id   bigint auto_increment primary key,
    permission_code varchar(100) not null,
    description     varchar(255) null,
    constraint uk_admin_permission_code unique (permission_code)
) comment 'administrator permission' charset = utf8mb4;

create table if not exists admin_account_role
(
    admin_id bigint not null,
    role_id  bigint not null,
    primary key (admin_id, role_id),
    constraint fk_admin_account_role_admin foreign key (admin_id) references admin_account (admin_id),
    constraint fk_admin_account_role_role foreign key (role_id) references admin_role (role_id)
) comment 'administrator role binding' charset = utf8mb4;

create table if not exists admin_role_permission
(
    role_id       bigint not null,
    permission_id bigint not null,
    primary key (role_id, permission_id),
    constraint fk_admin_role_permission_role foreign key (role_id) references admin_role (role_id),
    constraint fk_admin_role_permission_permission foreign key (permission_id)
        references admin_permission (permission_id)
) comment 'role permission binding' charset = utf8mb4;

create table if not exists admin_security_audit_log
(
    audit_id      bigint auto_increment primary key,
    actor_admin_id bigint       null,
    action        varchar(64)   not null,
    target_admin_id bigint      null,
    detail_json   json          null,
    created_at    datetime(3) default current_timestamp(3) not null,
    key idx_admin_security_audit_actor (actor_admin_id, created_at),
    key idx_admin_security_audit_target (target_admin_id, created_at)
) comment 'administrator security audit trail' charset = utf8mb4;

insert into admin_role (role_code, role_name, description) values
    ('SUPER_ADMIN', '超级管理员', '全权限及管理员治理'),
    ('AI_OPERATOR', 'AI运营', 'AI配置、评测和试用批次'),
    ('SUPPORT_AGENT', '客服专员', '脱敏客服查询与处置'),
    ('DATA_ANALYST', '数据分析', '聚合指标和匿名报告'),
    ('AUDITOR', '审计员', '只读审计') as incoming
on duplicate key update role_name = incoming.role_name, description = incoming.description;

insert into admin_permission (permission_code, description) values
    ('admin:manage', '管理员、角色和会话治理'),
    ('admin:legacy', '未细分的既有管理功能'),
    ('ai:config', 'AI配置和知识库管理'),
    ('ai:evaluate', 'AI评测和证据复核'),
    ('ai:pilot', '试用批次管理'),
    ('support:read', '脱敏客服数据读取'),
    ('support:write', '客服认领、回复和结案'),
    ('analytics:read', '聚合指标读取'),
    ('analytics:export', '匿名指标导出'),
    ('audit:read', '只读审计') as incoming
on duplicate key update description = incoming.description;

insert ignore into admin_role_permission (role_id, permission_id)
select r.role_id, p.permission_id from admin_role r cross join admin_permission p
where r.role_code = 'SUPER_ADMIN';

insert ignore into admin_role_permission (role_id, permission_id)
select r.role_id, p.permission_id from admin_role r join admin_permission p
  on p.permission_code in ('ai:config', 'ai:evaluate', 'ai:pilot', 'analytics:read')
where r.role_code = 'AI_OPERATOR';

insert ignore into admin_role_permission (role_id, permission_id)
select r.role_id, p.permission_id from admin_role r join admin_permission p
  on p.permission_code in ('support:read', 'support:write')
where r.role_code = 'SUPPORT_AGENT';

insert ignore into admin_role_permission (role_id, permission_id)
select r.role_id, p.permission_id from admin_role r join admin_permission p
  on p.permission_code in ('analytics:read', 'analytics:export')
where r.role_code = 'DATA_ANALYST';

insert ignore into admin_role_permission (role_id, permission_id)
select r.role_id, p.permission_id from admin_role r join admin_permission p
  on p.permission_code = 'audit:read'
where r.role_code = 'AUDITOR';

create table if not exists user_info
(
    user_id         varchar(10)          not null comment '用户id' primary key,
    nick_name       varchar(20)          not null comment '昵称',
    avatar          varchar(100)         null comment '头像',
    email           varchar(150)         not null comment '邮箱',
    password        varchar(100)         not null,
    sex             tinyint(1)           null comment '0:女 1:男 2:未知',
    join_time       datetime             not null comment '加入时间',
    last_login_time datetime             null comment '最后登录时间',
    last_login_ip   varchar(100)         null comment '最后登录IP',
    status          tinyint(1) default 1 not null comment '0:禁用 1:正常',
    temp_ban_until_ms bigint              null comment '临时封禁到期 epoch millis；NULL 表示非临时封禁',
    constraint idx_key_email unique (email),
    constraint idx_nick_name unique (nick_name),
    key idx_user_temp_ban_due (status, temp_ban_until_ms)
) comment '用户信息' collate = utf8mb4_general_ci row_format = DYNAMIC;

SET @sql = IF(
    EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'user_info'
          AND column_name = 'temp_ban_until_ms'
    ),
    'SELECT 1',
    'ALTER TABLE user_info ADD COLUMN temp_ban_until_ms bigint NULL COMMENT ''temporary ban expiry epoch millis'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'user_info'
          AND index_name = 'idx_user_temp_ban_due'
    ),
    'SELECT 1',
    'ALTER TABLE user_info ADD INDEX idx_user_temp_ban_due (status, temp_ban_until_ms)'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

create table if not exists user_address
(
    address_id   varchar(15)  not null comment '地址ID' primary key,
    user_id      varchar(15)  null comment '用户ID',
    address      varchar(300) null comment '详细地址',
    addressee    varchar(25)  null comment '收货人',
    phone        varchar(15)  null comment '手机号码',
    default_type tinyint      null comment '默认类型0:非默认  1:默认'
) collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists user_member_profile
(
    user_id      varchar(15)       not null primary key,
    level_code   tinyint default 1 not null comment '等级 1起',
    growth_value int     default 0 not null comment '成长值',
    level_name   varchar(30)       null,
    update_time  datetime          null
) comment '会员成长' collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists user_member_level_reward_claim
(
    user_id        varchar(15)  not null,
    level_code     tinyint      not null,
    user_coupon_id varchar(32)  null,
    bonus_growth   int default 0 not null,
    create_time    datetime     not null,
    primary key (user_id, level_code)
) comment '会员等级奖励领取幂等账本' collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists user_order_growth
(
    order_id     varchar(32)                         not null primary key,
    user_id      varchar(15)                         not null,
    pay_amount   decimal(10, 2)                      not null,
    growth_value int                                 not null,
    create_time  datetime default current_timestamp  not null,
    key idx_user_order_growth_user (user_id, create_time)
) comment '订单完成成长值幂等账本' collate = utf8mb4_general_ci row_format = DYNAMIC;

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
) comment '本地消息 Outbox（用户通知与临时封禁）' charset = utf8mb4;

-- 公共 MQ 消费失败审查组件使用用户库数据源；确保最终死信仍可落库审计。
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
    constraint uk_mq_compensation_idempotency unique (idempotency_key),
    key idx_mq_compensation_status (status),
    key idx_mq_compensation_scene (biz_scene),
    key idx_mq_compensation_create (create_time)
) comment 'MQ消费失败审查日志（用户库）' charset = utf8mb4;

create table if not exists user_sign_record
(
    user_id         varchar(15)   not null primary key,
    continuous_days int           not null comment '连续签到天数',
    total_sign_days int default 1 not null comment '总签到天数',
    used_count      int           not null comment '已使用补签次数'
) comment '签到记录' collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists user_sign_record_detail
(
    id          bigint auto_increment comment '主键' primary key,
    user_id     varchar(32)       not null comment '用户ID',
    sign_date   char(8)           not null comment '签到日期 yyyyMMdd',
    sign_type   tinyint default 0 not null comment '0普通签到 1补签',
    create_time datetime          not null comment '创建时间',
    constraint uk_user_sign_date unique (user_id, sign_date)
) comment '用户签到明细' charset = utf8mb4;

create table if not exists user_notification
(
    notification_id varchar(32)          not null primary key,
    user_id         varchar(15)          not null,
    title           varchar(100)         not null,
    content         varchar(500)         null,
    biz_type        varchar(30)          null comment 'order/coupon/system',
    biz_id          varchar(32)          null,
    read_status     tinyint(1) default 0 not null comment '0未读 1已读',
    create_time     datetime             not null
) comment '用户站内通知' collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists user_product_favorite
(
    favorite_id varchar(32) not null comment '收藏ID' primary key,
    user_id     varchar(15) not null comment '用户ID',
    product_id  varchar(15) not null comment '商品ID',
    create_time datetime    not null comment '收藏时间',
    constraint uk_favorite_user_product unique (user_id, product_id)
) comment '用户商品收藏' collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists user_browse_history
(
    history_id  bigint auto_increment comment '记录ID' primary key,
    user_id     varchar(15) not null comment '用户ID',
    product_id  varchar(15) not null comment '商品ID',
    browse_time datetime    not null comment '浏览时间',
    constraint uk_browse_user_product unique (user_id, product_id)
) comment '浏览足迹' collate = utf8mb4_general_ci row_format = DYNAMIC;

-- 图像审核写入侧在 user 服务（见 ImageModeration*）；admin 可只读/复核
create table if not exists image_moderation_record
(
    record_id       int auto_increment comment '记录ID' primary key,
    user_id         varchar(32)   not null comment '上传用户ID',
    user_ip         varchar(64)   null comment '上传IP',
    image_path      varchar(512)  null comment '存储键；清理后置空',
    asset_id        varchar(64)   null comment '对外不透明图片资产ID',
    content_sha256  char(64)      null comment '规范化图片SHA-256',
    mime_type       varchar(64)   null comment '规范化媒体类型',
    image_width     int           null comment '规范化图片宽度',
    image_height    int           null comment '规范化图片高度',
    retention_class varchar(32) default 'STANDARD' not null comment 'STANDARD/QUERY_30D/SUPPORT_EVIDENCE',
    scene           varchar(32)   not null comment '场景：avatar/comment/agent',
    order_id        varchar(32)   null comment '关联订单ID（评论场景）',
    conclusion_type int           null comment '百度结论类型 1合规 2不合规 3疑似 4失败',
    conclusion      varchar(128)  null comment '百度结论文案',
    baidu_response  text          null comment '百度原始响应摘要',
    status          int default 0 not null comment '0待人工复核 1已通过 2确认违规 3误报驳回',
    create_time     datetime      not null comment '创建时间',
    handle_time     datetime      null comment '处理时间',
    handle_remark   varchar(512)  null comment '处理备注',
    expires_at      datetime      null comment '原图过期时间',
    purged_at       datetime      null comment '原图实际清理时间',
    constraint uk_image_asset_id unique (asset_id),
    key idx_image_asset_expiry (scene, retention_class, expires_at, purged_at)
) comment '图像内容审核记录' charset = utf8mb4;

SET @sql = IF(
    EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE()
            AND table_name = 'image_moderation_record' AND column_name = 'asset_id'),
    'SELECT 1',
    'ALTER TABLE image_moderation_record ADD COLUMN asset_id varchar(64) NULL COMMENT ''opaque image asset id'' AFTER image_path'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE()
            AND table_name = 'image_moderation_record' AND column_name = 'image_path'
            AND is_nullable = 'YES'),
    'SELECT 1',
    'ALTER TABLE image_moderation_record MODIFY COLUMN image_path varchar(512) NULL COMMENT ''storage key; null after retention purge'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE()
            AND table_name = 'image_moderation_record' AND column_name = 'content_sha256'),
    'SELECT 1',
    'ALTER TABLE image_moderation_record ADD COLUMN content_sha256 char(64) NULL AFTER asset_id, ADD COLUMN mime_type varchar(64) NULL AFTER content_sha256, ADD COLUMN image_width int NULL AFTER mime_type, ADD COLUMN image_height int NULL AFTER image_width, ADD COLUMN retention_class varchar(32) NOT NULL DEFAULT ''STANDARD'' AFTER image_height, ADD COLUMN expires_at datetime NULL AFTER handle_remark, ADD COLUMN purged_at datetime NULL AFTER expires_at'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema = DATABASE()
            AND table_name = 'image_moderation_record' AND index_name = 'uk_image_asset_id'),
    'SELECT 1',
    'ALTER TABLE image_moderation_record ADD UNIQUE KEY uk_image_asset_id (asset_id), ADD KEY idx_image_asset_expiry (scene, retention_class, expires_at, purged_at)'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE image_moderation_record
SET scene = 'agent',
    asset_id = COALESCE(asset_id, LOWER(REPLACE(UUID(), '-', ''))),
    retention_class = 'QUERY_30D',
    expires_at = COALESCE(expires_at, DATE_ADD(create_time, INTERVAL 30 DAY))
WHERE scene = 'support';
-- Complete indexes that were historically created outside CREATE TABLE.
SET @sql = IF(
    EXISTS (
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'user_address'
          AND index_name = 'idx_user_id'
    ),
    'SELECT 1',
    'ALTER TABLE user_address ADD INDEX idx_user_id (user_id)'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'user_sign_record_detail'
          AND index_name = 'idx_create_time'
    ),
    'SELECT 1',
    'ALTER TABLE user_sign_record_detail ADD INDEX idx_create_time (create_time)'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'user_sign_record_detail'
          AND index_name = 'idx_sign_date'
    ),
    'SELECT 1',
    'ALTER TABLE user_sign_record_detail ADD INDEX idx_sign_date (sign_date)'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'user_notification'
          AND index_name = 'idx_notification_user_read'
    ),
    'SELECT 1',
    'ALTER TABLE user_notification ADD INDEX idx_notification_user_read (user_id, read_status, create_time)'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'user_product_favorite'
          AND index_name = 'idx_favorite_user_time'
    ),
    'SELECT 1',
    'ALTER TABLE user_product_favorite ADD INDEX idx_favorite_user_time (user_id, create_time)'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'user_browse_history'
          AND index_name = 'idx_browse_user_time'
    ),
    'SELECT 1',
    'ALTER TABLE user_browse_history ADD INDEX idx_browse_user_time (user_id, browse_time)'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
