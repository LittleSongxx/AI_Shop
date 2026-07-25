-- Current schema owned by the search service.
create table if not exists search_hot_keyword
(
    keyword     varchar(100)         not null comment '热搜词' primary key,
    sort        int        default 0 not null comment '排序',
    status      tinyint(1) default 1 not null comment '0停用 1启用',
    update_time datetime             null
) comment '运营配置热搜' collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists user_search_keyword
(
    id          bigint auto_increment primary key,
    user_id     varchar(15)  null comment 'NULL=仅统计热搜',
    keyword     varchar(100) not null comment '关键词',
    search_time datetime     not null comment '搜索时间'
) comment '用户/全局搜索词' collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists rag_question
(
    question_id      int auto_increment comment '自增ID' primary key,
    question         varchar(150)  null comment '问题',
    normalized_question varchar(300) null comment '标准化精确问题',
    similar_question varchar(1000) null comment '相似问题',
    answer           text          null comment '答案',
    create_time      datetime      null comment '创建时间',
    category         varchar(64) default 'general' not null,
    language         varchar(16) default 'zh-CN' not null,
    channel          varchar(32) default 'all' not null,
    priority         int default 0 not null,
    version          int default 1 not null,
    effective_start  datetime null,
    effective_end    datetime null,
    publish_status   varchar(16) default 'PUBLISHED' not null,
    source           varchar(128) null,
    owner            varchar(100) null,
    hit_count        bigint default 0 not null,
    update_time      datetime default current_timestamp not null on update current_timestamp,
    key idx_rag_exact (normalized_question, language, channel, publish_status),
    key idx_rag_publish (publish_status, effective_start, effective_end, priority)
) comment 'rag问题' collate = utf8mb4_general_ci row_format = DYNAMIC;

create table if not exists knowledge_document
(
    document_id bigint auto_increment primary key,
    title varchar(200) not null,
    file_type varchar(16) not null,
    source_name varchar(255) null,
    content_hash varchar(64) not null,
    normalized_text longtext not null,
    status varchar(16) not null,
    version int default 1 not null,
    owner varchar(100) null,
    effective_start datetime null,
    effective_end datetime null,
    error_message varchar(512) null,
    created_at datetime default current_timestamp not null,
    updated_at datetime default current_timestamp not null on update current_timestamp,
    unique key uk_knowledge_hash (content_hash),
    key idx_knowledge_status (status, updated_at)
) comment '规范化知识文档' collate = utf8mb4_general_ci;

create table if not exists knowledge_chunk
(
    chunk_id varchar(64) primary key,
    document_id bigint not null,
    chunk_index int not null,
    heading varchar(255) null,
    content text not null,
    metadata_json json null,
    token_count int default 0 not null,
    version int default 1 not null,
    status varchar(16) default 'DRAFT' not null,
    created_at datetime default current_timestamp not null,
    updated_at datetime default current_timestamp not null on update current_timestamp,
    unique key uk_knowledge_chunk (document_id, version, chunk_index),
    key idx_chunk_document (document_id, status)
) comment '知识文档切片' collate = utf8mb4_general_ci;

create table if not exists knowledge_ingest_job
(
    job_id bigint auto_increment primary key,
    document_id bigint not null,
    status varchar(16) not null,
    stage varchar(32) null,
    progress int default 0 not null,
    chunk_count int default 0 not null,
    error_message varchar(512) null,
    created_at datetime default current_timestamp not null,
    updated_at datetime default current_timestamp not null on update current_timestamp,
    key idx_ingest_job_status (status, updated_at)
) comment '知识入库任务' collate = utf8mb4_general_ci;

create table if not exists faq_candidate
(
    candidate_id bigint auto_increment primary key,
    question varchar(300) not null,
    normalized_hash varchar(64) not null,
    answer text not null,
    category varchar(64) default 'general' not null,
    source_message_id int null,
    frequency int default 1 not null,
    status varchar(16) default 'PENDING' not null,
    reviewer varchar(100) null,
    review_remark varchar(500) null,
    created_at datetime default current_timestamp not null,
    updated_at datetime default current_timestamp not null on update current_timestamp,
    unique key uk_faq_candidate_hash (normalized_hash),
    key idx_faq_candidate_status (status, frequency, created_at)
) comment '人工审核FAQ候选' collate = utf8mb4_general_ci;

create table if not exists knowledge_release
(
    release_key varchar(32) primary key,
    current_version bigint default 1 not null,
    updated_at datetime default current_timestamp not null on update current_timestamp
) comment '知识缓存失效版本' collate = utf8mb4_general_ci;

insert into knowledge_release (release_key, current_version)
values ('global', 1)
on duplicate key update release_key = values(release_key);

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
-- Add all columns missing from the pre-RAG search schema in one conditional DDL.
SET @add_columns = (
    SELECT GROUP_CONCAT(definition ORDER BY seq SEPARATOR ', ')
    FROM (
        SELECT 1 AS seq, 'ADD COLUMN normalized_question varchar(300) NULL' AS definition
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'rag_question'
              AND column_name = 'normalized_question'
        )
        UNION ALL
        SELECT 2, 'ADD COLUMN category varchar(64) DEFAULT ''general'' NOT NULL'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'rag_question'
              AND column_name = 'category'
        )
        UNION ALL
        SELECT 3, 'ADD COLUMN language varchar(16) DEFAULT ''zh-CN'' NOT NULL'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'rag_question'
              AND column_name = 'language'
        )
        UNION ALL
        SELECT 4, 'ADD COLUMN channel varchar(32) DEFAULT ''all'' NOT NULL'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'rag_question'
              AND column_name = 'channel'
        )
        UNION ALL
        SELECT 5, 'ADD COLUMN priority int DEFAULT 0 NOT NULL'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'rag_question'
              AND column_name = 'priority'
        )
        UNION ALL
        SELECT 6, 'ADD COLUMN version int DEFAULT 1 NOT NULL'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'rag_question'
              AND column_name = 'version'
        )
        UNION ALL
        SELECT 7, 'ADD COLUMN effective_start datetime NULL'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'rag_question'
              AND column_name = 'effective_start'
        )
        UNION ALL
        SELECT 8, 'ADD COLUMN effective_end datetime NULL'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'rag_question'
              AND column_name = 'effective_end'
        )
        UNION ALL
        SELECT 9, 'ADD COLUMN publish_status varchar(16) DEFAULT ''PUBLISHED'' NOT NULL'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'rag_question'
              AND column_name = 'publish_status'
        )
        UNION ALL
        SELECT 10, 'ADD COLUMN source varchar(128) NULL'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'rag_question'
              AND column_name = 'source'
        )
        UNION ALL
        SELECT 11, 'ADD COLUMN owner varchar(100) NULL'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'rag_question'
              AND column_name = 'owner'
        )
        UNION ALL
        SELECT 12, 'ADD COLUMN hit_count bigint DEFAULT 0 NOT NULL'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'rag_question'
              AND column_name = 'hit_count'
        )
        UNION ALL
        SELECT 13, 'ADD COLUMN update_time datetime DEFAULT CURRENT_TIMESTAMP NOT NULL ON UPDATE CURRENT_TIMESTAMP'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'rag_question'
              AND column_name = 'update_time'
        )
    ) missing_columns
);
SET @sql = IF(
    @add_columns IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE rag_question ', @add_columns)
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @add_indexes = (
    SELECT GROUP_CONCAT(definition ORDER BY seq SEPARATOR ', ')
    FROM (
        SELECT 1 AS seq, 'ADD INDEX idx_rag_exact (normalized_question, language, channel, publish_status)' AS definition
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.statistics
            WHERE table_schema = DATABASE() AND table_name = 'rag_question'
              AND index_name = 'idx_rag_exact'
        )
        UNION ALL
        SELECT 2, 'ADD INDEX idx_rag_publish (publish_status, effective_start, effective_end, priority)'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.statistics
            WHERE table_schema = DATABASE() AND table_name = 'rag_question'
              AND index_name = 'idx_rag_publish'
        )
    ) missing_indexes
);
SET @sql = IF(
    @add_indexes IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE rag_question ', @add_indexes)
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Outbox lease fields for old search databases.
SET @add_outbox_columns = (
    SELECT GROUP_CONCAT(definition ORDER BY seq SEPARATOR ', ')
    FROM (
        SELECT 1 AS seq, 'ADD COLUMN lease_owner varchar(64) NULL' AS definition
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'local_message_outbox'
              AND column_name = 'lease_owner'
        )
        UNION ALL
        SELECT 2, 'ADD COLUMN lease_until datetime NULL'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'local_message_outbox'
              AND column_name = 'lease_until'
        )
        UNION ALL
        SELECT 3, 'ADD COLUMN next_retry_time datetime NULL'
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'local_message_outbox'
              AND column_name = 'next_retry_time'
        )
    ) missing_outbox_columns
);
SET @sql = IF(
    @add_outbox_columns IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE local_message_outbox ', @add_outbox_columns)
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
    EXISTS (
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'local_message_outbox'
          AND index_name = 'idx_outbox_dispatch'
    ),
    'SELECT 1',
    'ALTER TABLE local_message_outbox ADD INDEX idx_outbox_dispatch (status, next_retry_time, lease_until, id)'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE rag_question
SET normalized_question = LOWER(
    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
        question, ' ', ''), '，', ''), '。', ''), '？', ''), '?', ''), '！', '')
)
WHERE normalized_question IS NULL;
