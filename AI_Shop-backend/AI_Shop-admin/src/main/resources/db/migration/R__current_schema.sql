-- Current schema owned by the admin service.
-- Analytics views apply utf8mb4 collations to literals and cross-schema columns.
-- Pin the session charset so manual/bootstrap execution does not inherit latin1.
SET NAMES utf8mb4 COLLATE utf8mb4_general_ci;

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

CREATE OR REPLACE SQL SECURITY DEFINER VIEW analytics_recommendation_funnel_daily AS
SELECT e.`date`,
       e.retrieval_mode,
       SUM(e.event_type = 'IMPRESSION') AS impression_count,
       SUM(e.event_type = 'CLICK') AS click_count,
       SUM(e.event_type = 'ADD_TO_CART') AS add_to_cart_count,
       SUM(e.event_type = 'PAYMENT') AS payment_count,
       ROUND(SUM(e.event_type = 'CLICK') / GREATEST(SUM(e.event_type = 'IMPRESSION'), 1), 4)
           AS click_through_rate,
       ROUND(SUM(e.event_type = 'ADD_TO_CART') / GREATEST(SUM(e.event_type = 'IMPRESSION'), 1), 4)
           AS cart_rate,
       ROUND(SUM(e.event_type = 'PAYMENT') / GREATEST(SUM(e.event_type = 'IMPRESSION'), 1), 4)
           AS payment_rate
  FROM (
        SELECT DATE(r.occurred_at) AS `date`,
               COALESCE(r.retrieval_mode, 'text') AS retrieval_mode,
               r.event_type
          FROM aishop_agent.agent_recommendation_event r
         WHERE r.event_type IN ('IMPRESSION', 'CLICK')
        UNION ALL
        SELECT DATE(o.occurred_at) AS `date`,
               COALESCE(
                   JSON_UNQUOTE(JSON_EXTRACT(o.payload_json, '$.attribution.retrievalMode')),
                   'text'
               ) AS retrieval_mode,
               o.event_type
          FROM aishop_agent.commerce_outcome_ledger o
         WHERE o.event_type IN ('ADD_TO_CART', 'PAYMENT')
           AND JSON_UNQUOTE(JSON_EXTRACT(o.payload_json, '$.attributionStatus')) = 'VERIFIED'
       ) e
 GROUP BY e.`date`, e.retrieval_mode;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW analytics_recommendation_quality_daily AS
SELECT DATE(o.occurred_at) AS `date`,
       o.product_id,
       SUM(o.event_type = 'PAYMENT') AS payment_count,
       SUM(o.event_type = 'REFUND') AS refund_count,
       SUM(o.event_type = 'RETURN') AS return_count,
       SUM(
           o.event_type = 'REVIEW'
           AND CAST(JSON_UNQUOTE(JSON_EXTRACT(o.payload_json, '$.rating')) AS UNSIGNED) <= 2
       ) AS negative_review_count,
       SUM(o.event_type = 'SUPPORT_CONTACT') AS support_contact_count,
       SUM(o.event_type = 'REPEAT_PURCHASE') AS repeat_purchase_count
  FROM aishop_agent.commerce_outcome_ledger o
 WHERE o.event_type IN (
           'PAYMENT', 'REFUND', 'RETURN', 'REVIEW', 'SUPPORT_CONTACT', 'REPEAT_PURCHASE'
       )
   AND JSON_UNQUOTE(JSON_EXTRACT(o.payload_json, '$.attributionStatus')) = 'VERIFIED'
 GROUP BY DATE(o.occurred_at), o.product_id;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW analytics_offer_quality_daily AS
SELECT DATE(s.created_at) AS `date`,
       s.product_id,
       COUNT(*) AS quote_count,
       SUM(JSON_UNQUOTE(JSON_EXTRACT(s.offer_json, '$.couponStatus')) = 'AVAILABLE')
           AS coupon_available_count,
       AVG(CAST(JSON_UNQUOTE(JSON_EXTRACT(s.offer_json, '$.basePrice')) AS DECIMAL(18, 2)))
           AS avg_base_price,
       AVG(CAST(JSON_UNQUOTE(JSON_EXTRACT(s.offer_json, '$.estimatedPayable')) AS DECIMAL(18, 2)))
           AS avg_estimated_payable,
       SUM(JSON_UNQUOTE(JSON_EXTRACT(s.offer_json, '$.inStock')) = 'true')
           AS in_stock_quote_count
  FROM aishop_agent.agent_final_offer_snapshot s
 GROUP BY DATE(s.created_at), s.product_id;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW analytics_fulfillment_after_sales_daily AS
SELECT d.`date`,
       COALESCE(o.paid_order_count, 0) AS paid_order_count,
       COALESCE(o.shipped_order_count, 0) AS shipped_order_count,
       COALESCE(o.completed_order_count, 0) AS completed_order_count,
       COALESCE(o.cancelled_order_count, 0) AS cancelled_order_count,
       COALESCE(rr.refund_request_count, 0) AS refund_request_count,
       COALESCE(rc.refund_completed_count, 0) AS refund_completed_count,
       COALESCE(rc.refund_completed_amount, 0) AS refund_completed_amount
  FROM (
        SELECT DATE(order_time) AS `date` FROM aishop_order.order_info
        UNION
        SELECT DATE(created_at) AS `date` FROM aishop_order.refund_request
        UNION
        SELECT DATE(completed_at) AS `date`
          FROM aishop_order.refund_request
         WHERE status = 'COMPLETED' AND completed_at IS NOT NULL
       ) d
  LEFT JOIN (
        SELECT DATE(order_time) AS `date`,
               SUM(order_status IN (1, 2, 3, 6, 7, 8)) AS paid_order_count,
               SUM(order_status = 2) AS shipped_order_count,
               SUM(order_status IN (3, 8)) AS completed_order_count,
               SUM(order_status IN (4, 5)) AS cancelled_order_count
          FROM aishop_order.order_info
         GROUP BY DATE(order_time)
       ) o ON o.`date` = d.`date`
  LEFT JOIN (
        SELECT DATE(created_at) AS `date`,
               COUNT(*) AS refund_request_count
          FROM aishop_order.refund_request
         GROUP BY DATE(created_at)
       ) rr ON rr.`date` = d.`date`
  LEFT JOIN (
        SELECT DATE(completed_at) AS `date`,
               COUNT(*) AS refund_completed_count,
               SUM(refund_amount) AS refund_completed_amount
          FROM aishop_order.refund_request
         WHERE status = 'COMPLETED' AND completed_at IS NOT NULL
         GROUP BY DATE(completed_at)
       ) rc ON rc.`date` = d.`date`;

CREATE OR REPLACE SQL SECURITY DEFINER VIEW analytics_inventory_forecast AS
WITH RECURSIVE calendar AS (
    SELECT CURRENT_DATE - INTERVAL 27 DAY AS day_date
    UNION ALL
    SELECT day_date + INTERVAL 1 DAY
      FROM calendar
     WHERE day_date < CURRENT_DATE
),
demand_events AS (
    SELECT DATE(o.order_time) AS day_date,
           oi.product_id COLLATE utf8mb4_general_ci AS product_id,
           oi.property_value_id_hash COLLATE utf8mb4_general_ci AS sku_key,
           SUM(oi.buy_count) AS net_units
      FROM aishop_order.order_info o
      JOIN aishop_order.order_item oi
        ON oi.order_id COLLATE utf8mb4_general_ci = o.order_id COLLATE utf8mb4_general_ci
     WHERE o.order_status IN (1, 2, 3, 6, 7, 8)
       AND o.order_time >= DATE_SUB(CURRENT_DATE, INTERVAL 27 DAY)
     GROUP BY DATE(o.order_time), oi.product_id, oi.property_value_id_hash
    UNION ALL
    SELECT DATE(rr.completed_at) AS day_date,
           rr.product_id COLLATE utf8mb4_general_ci AS product_id,
           oi.property_value_id_hash COLLATE utf8mb4_general_ci AS sku_key,
           -SUM(rr.buy_count) AS net_units
      FROM aishop_order.refund_request rr
      JOIN aishop_order.order_item oi
        ON oi.order_item_id COLLATE utf8mb4_general_ci =
           rr.order_item_id COLLATE utf8mb4_general_ci
     WHERE rr.status = 'COMPLETED'
       AND rr.completed_at >= DATE_SUB(CURRENT_DATE, INTERVAL 27 DAY)
     GROUP BY DATE(rr.completed_at), rr.product_id, oi.property_value_id_hash
),
daily_demand AS (
    SELECT i.product_id COLLATE utf8mb4_general_ci AS product_id,
           i.property_value_id_hash COLLATE utf8mb4_general_ci AS sku_key,
           c.day_date,
           COALESCE(SUM(e.net_units), 0) AS net_units,
           CASE WHEN COUNT(e.product_id) = 0 THEN 0 ELSE 1 END AS observed
      FROM analytics_inventory_risk i
      CROSS JOIN calendar c
      LEFT JOIN demand_events e
        ON e.product_id = i.product_id COLLATE utf8mb4_general_ci
       AND e.sku_key = i.property_value_id_hash COLLATE utf8mb4_general_ci
       AND e.day_date = c.day_date
     GROUP BY i.product_id, i.property_value_id_hash, c.day_date
),
demand AS (
    SELECT product_id,
           sku_key,
           GREATEST(
               SUM(net_units * POW(0.90, DATEDIFF(CURRENT_DATE, day_date)))
                   / NULLIF(SUM(POW(0.90, DATEDIFF(CURRENT_DATE, day_date))), 0),
               0
           ) AS ewma_daily_demand,
           SUM(observed) AS observed_days
      FROM daily_demand
     GROUP BY product_id, sku_key
)
SELECT CURRENT_DATE AS snapshot_date,
       i.product_id,
       i.product_name,
       i.property_value_id_hash AS sku_key,
       i.risk_level,
       i.stock AS current_stock,
       COALESCE(b.inbound_quantity, 0) AS inbound_quantity,
       ROUND(COALESCE(d.ewma_daily_demand, 0), 4) AS ewma_daily_demand,
       COALESCE(p.lead_time_days, 7) AS lead_time_days,
       COALESCE(p.safety_stock, 0) AS safety_stock,
       COALESCE(p.review_period_days, 14) AS review_period_days,
       GREATEST(COALESCE(p.min_order_quantity, 1), 1) AS min_order_quantity,
       ROUND(
           COALESCE(d.ewma_daily_demand, 0) * COALESCE(p.lead_time_days, 7)
           + COALESCE(p.safety_stock, 0),
           2
       ) AS reorder_point,
       CEIL(
           GREATEST(
               0,
               COALESCE(d.ewma_daily_demand, 0)
                   * (COALESCE(p.lead_time_days, 7) + COALESCE(p.review_period_days, 14))
                   + COALESCE(p.safety_stock, 0)
                   - i.stock
                   - COALESCE(b.inbound_quantity, 0)
           ) / GREATEST(COALESCE(p.min_order_quantity, 1), 1)
       ) * GREATEST(COALESCE(p.min_order_quantity, 1), 1)
           AS suggested_replenish_quantity,
       CASE
           WHEN COALESCE(d.ewma_daily_demand, 0) <= 0 THEN NULL
           ELSE ROUND((i.stock + COALESCE(b.inbound_quantity, 0)) / d.ewma_daily_demand, 2)
       END AS coverage_days,
       ROUND(LEAST(COALESCE(d.observed_days, 0) / 28, 1), 4) AS confidence
  FROM analytics_inventory_risk i
  LEFT JOIN aishop_agent.agent_inventory_supply_parameter p
    ON p.product_id COLLATE utf8mb4_general_ci = i.product_id COLLATE utf8mb4_general_ci
   AND p.sku_key COLLATE utf8mb4_general_ci =
       i.property_value_id_hash COLLATE utf8mb4_general_ci
   AND p.enabled = 1
  LEFT JOIN (
        SELECT product_id, sku_key, SUM(quantity) AS inbound_quantity
          FROM aishop_agent.agent_inventory_inbound
         WHERE status IN ('PLANNED', 'IN_TRANSIT')
           AND (eta_date IS NULL OR eta_date >= CURRENT_DATE)
         GROUP BY product_id, sku_key
       ) b ON b.product_id COLLATE utf8mb4_general_ci = i.product_id COLLATE utf8mb4_general_ci
          AND b.sku_key COLLATE utf8mb4_general_ci =
              i.property_value_id_hash COLLATE utf8mb4_general_ci
  LEFT JOIN demand d
    ON d.product_id COLLATE utf8mb4_general_ci = i.product_id COLLATE utf8mb4_general_ci
   AND d.sku_key COLLATE utf8mb4_general_ci =
       i.property_value_id_hash COLLATE utf8mb4_general_ci;

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
