USE simlect_search;
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
