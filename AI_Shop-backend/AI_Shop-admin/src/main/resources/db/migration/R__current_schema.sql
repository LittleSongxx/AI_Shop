-- Current schema owned by the admin service.
create table if not exists admin_audit_log
(
    id             bigint auto_increment comment '主键' primary key,
    operator       varchar(64)   not null comment '操作人账号',
    action         varchar(64)   not null comment '动作标识',
    target_user_id varchar(32)   null comment '目标用户ID',
    detail         varchar(2000) null comment '详情 JSON/文本',
    create_time    datetime      not null comment '创建时间'
) comment '管理端操作审计' charset = utf8mb4;

create table if not exists statistics_info
(
    statistics_date varchar(10)    not null comment '日期',
    data_type       tinyint(1)     not null comment '数据类型',
    data_value      decimal(10, 2) null comment '统计数据',
    primary key (statistics_date, data_type)
) comment '数据统计结果' collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists sensitive_word
(
    id           bigint auto_increment primary key,
    word         varchar(128)                           not null comment '敏感词',
    replace_word varchar(128) default '***'             null comment '替换词',
    status       tinyint      default 1                 null comment '状态：1启用 0停用',
    create_time  datetime     default CURRENT_TIMESTAMP null,
    update_time  datetime     default CURRENT_TIMESTAMP null on update CURRENT_TIMESTAMP,
    constraint uk_word unique (word)
) comment '敏感词表' charset = utf8mb4;

create table if not exists mq_compensation_log
(
    log_id            int auto_increment comment '日志ID' primary key,
    idempotency_key   varchar(128)               not null comment '幂等键',
    exchange          varchar(64)                not null comment '交换机',
    routing_key       varchar(64)                not null comment '路由键',
    biz_scene         varchar(32)                null comment '业务场景：RAG/NOTIFY/BROWSE/SIGN/PAY/OTHER',
    payload_json      mediumtext                 null comment '消息体 JSON',
    reliability_level varchar(16) default 'HIGH' not null comment '发送级别 HIGH/STANDARD',
    error_message     varchar(512)               null comment '失败原因',
    retry_count       int         default 0      not null comment '重放次数',
    status            int         default 0      not null comment '0待处理 1处理中 2已重放成功 3重放失败 4已忽略',
    create_time       datetime                   not null comment '创建时间',
    update_time       datetime                   null comment '更新时间',
    handle_time       datetime                   null comment '运维处理时间',
    handle_remark     varchar(512)               null comment '处理备注',
    constraint uk_idempotency_key unique (idempotency_key)
) comment 'MQ补偿审查日志' charset = utf8mb4;

create table if not exists image_moderation_record
(
    record_id       int auto_increment comment '记录ID' primary key,
    user_id         varchar(32)   not null comment '上传用户ID',
    user_ip         varchar(64)   null comment '上传IP',
    image_path      varchar(512)  not null comment '图片相对路径',
    scene           varchar(32)   not null comment '场景：avatar/comment',
    order_id        varchar(32)   null comment '关联订单ID（评论场景）',
    conclusion_type int           null comment '百度结论类型 1合规 2不合规 3疑似 4失败',
    conclusion      varchar(128)  null comment '百度结论文案',
    baidu_response  text          null comment '百度原始响应摘要',
    status          int default 0 not null comment '0待人工复核 1已通过 2确认违规 3误报驳回',
    create_time     datetime      not null comment '创建时间',
    handle_time     datetime      null comment '处理时间',
    handle_remark   varchar(512)  null comment '处理备注'
) comment '图像内容审核记录' charset = utf8mb4;

create table if not exists local_message_outbox
(
    id                bigint auto_increment comment '主键' primary key,
    idempotency_key   varchar(128)                    not null comment '发送幂等键',
    exchange_name     varchar(64)                     not null comment '交换机',
    routing_key       varchar(64)                     not null comment '路由键',
    payload_json      mediumtext                      not null comment '消息体 JSON',
    reliability_level varchar(16) default 'STANDARD'  not null comment 'HIGH/STANDARD',
    status            tinyint     default 0           not null comment '0待发送 1发送中 2已发送 3失败',
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

-- Governed, PII-free semantic layer for the admin DataAnalyst. The runtime
-- identity receives SELECT on these views only, never on their source tables.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW analytics_sales_daily AS
SELECT d.`date`,
       COALESCE(p.paid_order_count, 0) AS paid_order_count,
       COALESCE(p.gross_paid_amount, 0) AS gross_paid_amount,
       COALESCE(r.completed_refund_count, 0) AS completed_refund_count,
       COALESCE(r.completed_refund_amount, 0) AS completed_refund_amount,
       COALESCE(p.gross_paid_amount, 0) - COALESCE(r.completed_refund_amount, 0) AS net_paid_amount
  FROM (
        SELECT DATE(order_time) AS `date`
          FROM aishop_order.order_info
         WHERE order_status IN (1, 2, 3, 6, 7, 8)
        UNION
        SELECT DATE(completed_at) AS `date`
          FROM aishop_order.refund_request
         WHERE status = 'COMPLETED' AND completed_at IS NOT NULL
       ) d
  LEFT JOIN (
        SELECT DATE(order_time) AS `date`,
               COUNT(*) AS paid_order_count,
               SUM(amount) AS gross_paid_amount
          FROM aishop_order.order_info
         WHERE order_status IN (1, 2, 3, 6, 7, 8)
         GROUP BY DATE(order_time)
       ) p ON p.`date` = d.`date`
  LEFT JOIN (
        SELECT DATE(completed_at) AS `date`,
               COUNT(*) AS completed_refund_count,
               SUM(refund_amount) AS completed_refund_amount
          FROM aishop_order.refund_request
         WHERE status = 'COMPLETED' AND completed_at IS NOT NULL
         GROUP BY DATE(completed_at)
       ) r ON r.`date` = d.`date`;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW analytics_product_sales_daily AS
SELECT k.`date`,
       k.product_id,
       COALESCE(s.product_name, r.product_name) AS product_name,
       COALESCE(s.paid_units, 0) AS paid_units,
       COALESCE(s.gross_item_amount, 0) AS gross_item_amount,
       COALESCE(r.refunded_units, 0) AS refunded_units
  FROM (
        SELECT DATE(o.order_time) AS `date`,
               i.product_id COLLATE utf8mb4_general_ci AS product_id
          FROM aishop_order.order_info o
          JOIN aishop_order.order_item i ON i.order_id = o.order_id
         WHERE o.order_status IN (1, 2, 3, 6, 7, 8)
        UNION
        SELECT DATE(rr.completed_at) AS `date`,
               rr.product_id COLLATE utf8mb4_general_ci AS product_id
          FROM aishop_order.refund_request rr
         WHERE rr.status = 'COMPLETED' AND rr.completed_at IS NOT NULL
       ) k
  LEFT JOIN (
        SELECT DATE(o.order_time) AS `date`,
               i.product_id COLLATE utf8mb4_general_ci AS product_id,
               MAX(i.product_name) AS product_name,
               SUM(i.buy_count) AS paid_units,
               SUM(i.item_amount) AS gross_item_amount
          FROM aishop_order.order_info o
          JOIN aishop_order.order_item i ON i.order_id = o.order_id
         WHERE o.order_status IN (1, 2, 3, 6, 7, 8)
         GROUP BY DATE(o.order_time), i.product_id COLLATE utf8mb4_general_ci
       ) s ON s.`date` = k.`date` AND s.product_id = k.product_id
  LEFT JOIN (
        SELECT DATE(rr.completed_at) AS `date`,
               rr.product_id COLLATE utf8mb4_general_ci AS product_id,
               MAX(i.product_name) AS product_name,
               SUM(rr.buy_count) AS refunded_units
          FROM aishop_order.refund_request rr
          LEFT JOIN aishop_order.order_item i
            ON i.order_item_id COLLATE utf8mb4_general_ci =
               rr.order_item_id COLLATE utf8mb4_general_ci
         WHERE rr.status = 'COMPLETED' AND rr.completed_at IS NOT NULL
         GROUP BY DATE(rr.completed_at), rr.product_id COLLATE utf8mb4_general_ci
       ) r ON r.`date` = k.`date` AND r.product_id = k.product_id;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW analytics_inventory_risk AS
SELECT CURRENT_DATE AS snapshot_date,
       s.product_id,
       p.product_name,
       s.property_value_id_hash,
       s.stock,
       (CASE
           WHEN s.stock <= 0 THEN 'OUT_OF_STOCK'
           WHEN s.stock <= 10 THEN 'LOW_STOCK'
           ELSE 'NORMAL'
       END) COLLATE utf8mb4_general_ci AS risk_level
  FROM aishop_stock.sku_stock s
  JOIN aishop_product.product_info p ON p.product_id = s.product_id
 WHERE p.status <> -1;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW analytics_agent_quality_daily AS
SELECT DATE(r.started_at) AS `date`,
       COALESCE(r.agent_id, 'supervisor') AS agent_id,
       COALESCE(r.intent, 'UNKNOWN') AS intent,
       COUNT(*) AS run_count,
       SUM(r.status = 'SUCCEEDED') AS success_count,
       SUM(r.status IN ('FAILED', 'CANCELLED')) AS failure_count,
       SUM(r.status = 'HANDOFF' OR r.outcome = 'human_support') AS human_handoff_count,
       AVG(r.latency_ms) AS avg_latency_ms,
       SUM(r.input_tokens) AS input_tokens,
       SUM(r.output_tokens) AS output_tokens,
       SUM(r.cost_cny) AS cost_cny
  FROM aishop_agent.agent_run r
 GROUP BY DATE(r.started_at), COALESCE(r.agent_id, 'supervisor'), COALESCE(r.intent, 'UNKNOWN');

CREATE OR REPLACE SQL SECURITY DEFINER VIEW analytics_tool_quality_daily AS
SELECT DATE(r.started_at) AS `date`,
       COALESCE(s.agent_id, r.agent_id, 'supervisor') AS agent_id,
       s.tool_name,
       COUNT(*) AS call_count,
       SUM(s.status IN ('OK', 'SUCCESS')) AS success_count,
       SUM(s.status NOT IN ('OK', 'SUCCESS')) AS failure_count,
       AVG(s.latency_ms) AS avg_latency_ms
  FROM aishop_agent.agent_step s
  JOIN aishop_agent.agent_run r ON r.run_id = s.run_id
 WHERE s.event_type = 'TOOL_CALL' AND s.tool_name IS NOT NULL
 GROUP BY DATE(r.started_at), COALESCE(s.agent_id, r.agent_id, 'supervisor'), s.tool_name;

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
