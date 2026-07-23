-- AI Shop one-time compatible upgrade.
-- Run against MySQL 8 after backing up all simlect_* databases.

USE simlect_order;

ALTER TABLE local_message_outbox
    ADD COLUMN lease_owner varchar(64) NULL COMMENT 'current dispatcher instance',
    ADD COLUMN lease_until datetime NULL COMMENT 'sending lease deadline',
    ADD COLUMN next_retry_time datetime NULL COMMENT 'next eligible retry time',
    ADD INDEX idx_outbox_dispatch (status, next_retry_time, lease_until, id);

CREATE TABLE refund_request
(
    refund_request_id  varchar(32)                         NOT NULL PRIMARY KEY,
    refund_order_no    varchar(32)                         NOT NULL,
    source_pay_order_id varchar(32)                        NOT NULL,
    order_id           varchar(32)                         NOT NULL,
    order_item_id      varchar(40)                         NOT NULL,
    user_id            varchar(15)                         NOT NULL,
    product_id         varchar(15)                         NOT NULL,
    property_value_id_hash varchar(32)                     NOT NULL,
    buy_count          int                                 NOT NULL,
    refund_amount      decimal(10, 2)                      NOT NULL,
    pay_channel        varchar(20)                         NULL,
    status             varchar(32)                         NOT NULL COMMENT 'PENDING_PAYMENT/PAYMENT_CONFIRMED/STOCK_PENDING/COMPLETED/MANUAL_REVIEW',
    retry_count        int       DEFAULT 0                 NOT NULL,
    next_retry_time    datetime                            NULL,
    last_error         varchar(512)                        NULL,
    created_at         datetime  DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at         datetime  DEFAULT CURRENT_TIMESTAMP NOT NULL ON UPDATE CURRENT_TIMESTAMP,
    payment_confirmed_at datetime                           NULL,
    completed_at       datetime                            NULL,
    CONSTRAINT uk_refund_order_no UNIQUE (refund_order_no),
    CONSTRAINT uk_refund_order_item UNIQUE (order_item_id),
    KEY idx_refund_retry (status, next_retry_time)
) COMMENT 'persistent refund saga' CHARSET = utf8mb4;

USE simlect_admin;

ALTER TABLE local_message_outbox
    ADD COLUMN lease_owner varchar(64) NULL COMMENT 'current dispatcher instance',
    ADD COLUMN lease_until datetime NULL COMMENT 'sending lease deadline',
    ADD COLUMN next_retry_time datetime NULL COMMENT 'next eligible retry time',
    ADD INDEX idx_outbox_dispatch (status, next_retry_time, lease_until, id);

USE simlect_user;

ALTER TABLE local_message_outbox
    ADD COLUMN lease_owner varchar(64) NULL COMMENT 'current dispatcher instance',
    ADD COLUMN lease_until datetime NULL COMMENT 'sending lease deadline',
    ADD COLUMN next_retry_time datetime NULL COMMENT 'next eligible retry time',
    ADD INDEX idx_outbox_dispatch (status, next_retry_time, lease_until, id);

USE simlect_product;

ALTER TABLE local_message_outbox
    ADD COLUMN lease_owner varchar(64) NULL COMMENT 'current dispatcher instance',
    ADD COLUMN lease_until datetime NULL COMMENT 'sending lease deadline',
    ADD COLUMN next_retry_time datetime NULL COMMENT 'next eligible retry time',
    ADD INDEX idx_outbox_dispatch (status, next_retry_time, lease_until, id);

USE simlect_search;

ALTER TABLE local_message_outbox
    ADD COLUMN lease_owner varchar(64) NULL COMMENT 'current dispatcher instance',
    ADD COLUMN lease_until datetime NULL COMMENT 'sending lease deadline',
    ADD COLUMN next_retry_time datetime NULL COMMENT 'next eligible retry time',
    ADD INDEX idx_outbox_dispatch (status, next_retry_time, lease_until, id);

USE simlect_stock;

CREATE TABLE stock_change_record
(
    business_key       varchar(96)                         NOT NULL PRIMARY KEY,
    change_type        varchar(32)                         NOT NULL,
    product_id         varchar(15)                         NOT NULL,
    property_value_id_hash varchar(32)                     NOT NULL,
    change_amount      int                                 NOT NULL,
    created_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL,
    KEY idx_stock_change_sku (product_id, property_value_id_hash)
) COMMENT 'idempotent stock changes' CHARSET = utf8mb4;

USE simlect_pay;

ALTER TABLE pay_trade_record
    ADD CONSTRAINT uk_pay_trade_pay_order UNIQUE (pay_order_id);

USE simlect_agent;

ALTER TABLE agent_message
    ADD COLUMN session_id varchar(36) NULL COMMENT 'support or AI conversation session',
    ADD COLUMN intent varchar(40) NULL,
    ADD COLUMN intent_confidence decimal(5, 4) NULL,
    ADD COLUMN sentiment varchar(20) NULL,
    ADD COLUMN urgency varchar(20) NULL,
    ADD COLUMN risk_level varchar(20) NULL,
    ADD COLUMN trace_id varchar(64) NULL,
    ADD COLUMN source_refs json NULL,
    ADD COLUMN latency_ms int NULL,
    ADD COLUMN unresolved_count int DEFAULT 0 NOT NULL,
    ADD COLUMN queue_name varchar(64) NULL,
    ADD INDEX idx_agent_message_session (session_id, message_id),
    ADD INDEX idx_agent_message_quality (intent, sentiment, send_time);

CREATE TABLE support_session
(
    session_id         varchar(36)                         NOT NULL PRIMARY KEY,
    user_id            varchar(15)                         NOT NULL,
    status             varchar(16)                         NOT NULL,
    trigger_reason     varchar(64)                         NULL,
    summary            text                                NULL,
    intent             varchar(40)                         NULL,
    sentiment          varchar(20)                         NULL,
    urgency            varchar(20)                         NULL,
    risk_level         varchar(20)                         NULL,
    assigned_admin     varchar(100)                        NULL,
    source_message_id  int                                 NULL,
    created_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL,
    assigned_at        datetime                            NULL,
    resolved_at        datetime                            NULL,
    updated_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_support_queue (status, urgency, created_at),
    KEY idx_support_user (user_id, status, updated_at)
) COMMENT 'lightweight human support session' CHARSET = utf8mb4;

CREATE TABLE support_message
(
    support_message_id bigint AUTO_INCREMENT PRIMARY KEY,
    session_id         varchar(36)                         NOT NULL,
    sender_type        varchar(16)                         NOT NULL COMMENT 'USER/ADMIN/AI/SYSTEM',
    sender_id          varchar(100)                        NULL,
    content            text                                NOT NULL,
    source_message_id  int                                 NULL,
    trace_id           varchar(64)                         NULL,
    created_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL,
    KEY idx_support_message_session (session_id, support_message_id)
) COMMENT 'human support transcript' CHARSET = utf8mb4;

CREATE TABLE agent_task
(
    task_id            bigint AUTO_INCREMENT PRIMARY KEY,
    message_id         int                                 NOT NULL,
    user_id            varchar(15)                         NOT NULL,
    queue_name         varchar(64)                         NOT NULL,
    priority           tinyint                             NOT NULL,
    status             varchar(16)                         NOT NULL,
    retry_count        int DEFAULT 0                       NOT NULL,
    deadline_at        datetime                            NULL,
    payload_json       json                                NOT NULL,
    error_message      varchar(512)                        NULL,
    created_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL,
    updated_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL ON UPDATE CURRENT_TIMESTAMP,
    started_at         datetime                            NULL,
    completed_at       datetime                            NULL,
    CONSTRAINT uk_agent_task_message UNIQUE (message_id),
    KEY idx_agent_task_dispatch (status, priority, created_at)
) COMMENT 'durable Agent task ledger' CHARSET = utf8mb4;

CREATE TABLE agent_message_feedback
(
    feedback_id        bigint AUTO_INCREMENT PRIMARY KEY,
    message_id         int                                 NOT NULL,
    user_id            varchar(15)                         NOT NULL,
    rating             tinyint                             NOT NULL COMMENT '1 useful, -1 not useful',
    reason             varchar(64)                         NULL,
    detail             varchar(500)                        NULL,
    created_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL,
    updated_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uk_agent_feedback UNIQUE (message_id, user_id)
) COMMENT 'user feedback for AI answers' CHARSET = utf8mb4;

CREATE TABLE ai_badcase_candidate
(
    candidate_id       bigint AUTO_INCREMENT PRIMARY KEY,
    message_id         int                                 NULL,
    candidate_type     varchar(32)                         NOT NULL,
    reason             varchar(255)                        NOT NULL,
    status             varchar(16) DEFAULT 'PENDING'       NOT NULL,
    snapshot_json      json                                NULL,
    reviewer           varchar(100)                        NULL,
    review_remark      varchar(500)                        NULL,
    created_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL,
    updated_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uk_badcase_message_type UNIQUE (message_id, candidate_type),
    KEY idx_badcase_status (status, created_at)
) COMMENT 'online badcase review pool' CHARSET = utf8mb4;

CREATE TABLE shopping_profile
(
    user_id            varchar(15)                         NOT NULL PRIMARY KEY,
    version            int DEFAULT 1                       NOT NULL,
    profile_json       json                                NOT NULL,
    updated_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL ON UPDATE CURRENT_TIMESTAMP
) COMMENT 'structured per-user shopping profile' CHARSET = utf8mb4;

CREATE TABLE user_recommend_event
(
    event_id           bigint AUTO_INCREMENT PRIMARY KEY,
    request_id         varchar(64)                         NOT NULL,
    user_id            varchar(15)                         NOT NULL,
    product_id         varchar(15)                         NOT NULL,
    event_type         varchar(16)                         NOT NULL,
    source             varchar(32)                         NULL,
    message_id         int                                 NULL,
    metadata_json      json                                NULL,
    event_time         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL,
    KEY idx_recommend_user_time (user_id, event_time),
    KEY idx_recommend_request (request_id, event_type)
) COMMENT 'recommendation exposure and conversion events' CHARSET = utf8mb4;

USE simlect_search;

ALTER TABLE rag_question
    ADD COLUMN normalized_question varchar(300) NULL,
    ADD COLUMN category varchar(64) DEFAULT 'general' NOT NULL,
    ADD COLUMN language varchar(16) DEFAULT 'zh-CN' NOT NULL,
    ADD COLUMN channel varchar(32) DEFAULT 'all' NOT NULL,
    ADD COLUMN priority int DEFAULT 0 NOT NULL,
    ADD COLUMN version int DEFAULT 1 NOT NULL,
    ADD COLUMN effective_start datetime NULL,
    ADD COLUMN effective_end datetime NULL,
    ADD COLUMN publish_status varchar(16) DEFAULT 'PUBLISHED' NOT NULL,
    ADD COLUMN source varchar(128) NULL,
    ADD COLUMN owner varchar(100) NULL,
    ADD COLUMN hit_count bigint DEFAULT 0 NOT NULL,
    ADD COLUMN update_time datetime DEFAULT CURRENT_TIMESTAMP NOT NULL ON UPDATE CURRENT_TIMESTAMP,
    ADD INDEX idx_rag_publish (publish_status, effective_start, effective_end, priority),
    ADD INDEX idx_rag_exact (normalized_question, language, channel, publish_status);

UPDATE rag_question
SET normalized_question = LOWER(
        REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
        question, ' ', ''), '，', ''), '。', ''), '？', ''), '?', ''), '！', '')
    )
WHERE normalized_question IS NULL;

CREATE TABLE knowledge_document
(
    document_id        bigint AUTO_INCREMENT PRIMARY KEY,
    title              varchar(200)                        NOT NULL,
    file_type          varchar(16)                         NOT NULL,
    source_name        varchar(255)                        NULL,
    content_hash       varchar(64)                         NOT NULL,
    normalized_text    longtext                            NOT NULL,
    status             varchar(16)                         NOT NULL,
    version            int DEFAULT 1                       NOT NULL,
    owner              varchar(100)                        NULL,
    effective_start    datetime                            NULL,
    effective_end      datetime                            NULL,
    error_message      varchar(512)                        NULL,
    created_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL,
    updated_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uk_knowledge_hash UNIQUE (content_hash),
    KEY idx_knowledge_status (status, updated_at)
) COMMENT 'normalized knowledge documents' CHARSET = utf8mb4;

CREATE TABLE knowledge_chunk
(
    chunk_id           varchar(64)                         NOT NULL PRIMARY KEY,
    document_id        bigint                              NOT NULL,
    chunk_index        int                                 NOT NULL,
    heading            varchar(255)                        NULL,
    content            text                                NOT NULL,
    metadata_json      json                                NULL,
    token_count        int DEFAULT 0                       NOT NULL,
    version            int DEFAULT 1                       NOT NULL,
    status             varchar(16) DEFAULT 'PUBLISHED'     NOT NULL,
    created_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL,
    updated_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uk_knowledge_chunk UNIQUE (document_id, version, chunk_index),
    KEY idx_chunk_document (document_id, status)
) COMMENT 'knowledge chunks ready for indexing' CHARSET = utf8mb4;

CREATE TABLE knowledge_ingest_job
(
    job_id             bigint AUTO_INCREMENT PRIMARY KEY,
    document_id        bigint                              NOT NULL,
    status             varchar(16)                         NOT NULL,
    stage              varchar(32)                         NULL,
    progress           int DEFAULT 0                       NOT NULL,
    chunk_count        int DEFAULT 0                       NOT NULL,
    error_message      varchar(512)                        NULL,
    created_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL,
    updated_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_ingest_job_status (status, updated_at)
) COMMENT 'knowledge ingestion jobs' CHARSET = utf8mb4;

CREATE TABLE faq_candidate
(
    candidate_id       bigint AUTO_INCREMENT PRIMARY KEY,
    question           varchar(300)                        NOT NULL,
    normalized_hash    varchar(64)                         NOT NULL,
    answer             text                                NOT NULL,
    category           varchar(64) DEFAULT 'general'       NOT NULL,
    source_message_id  int                                 NULL,
    frequency          int DEFAULT 1                       NOT NULL,
    status             varchar(16) DEFAULT 'PENDING'       NOT NULL,
    reviewer           varchar(100)                        NULL,
    review_remark      varchar(500)                        NULL,
    created_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL,
    updated_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uk_faq_candidate_hash UNIQUE (normalized_hash),
    KEY idx_faq_candidate_status (status, frequency, created_at)
) COMMENT 'human-reviewed FAQ candidates' CHARSET = utf8mb4;

CREATE TABLE knowledge_release
(
    release_key        varchar(32)                         NOT NULL PRIMARY KEY,
    current_version    bigint DEFAULT 1                    NOT NULL,
    updated_at         datetime DEFAULT CURRENT_TIMESTAMP  NOT NULL ON UPDATE CURRENT_TIMESTAMP
) COMMENT 'knowledge cache invalidation version' CHARSET = utf8mb4;

INSERT INTO knowledge_release (release_key, current_version)
VALUES ('global', 1)
ON DUPLICATE KEY UPDATE release_key = VALUES(release_key);
