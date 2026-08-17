"""Create and reconcile the complete current Agent schema."""

import sqlalchemy as sa
from alembic import op

revision = "current"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_message
        (
            message_id         int AUTO_INCREMENT PRIMARY KEY,
            assistant_message  text NULL,
            user_message       varchar(4000) NULL,
            send_time          datetime NULL,
            user_id            varchar(15) NULL,
            status             tinyint DEFAULT 1 NULL,
            biz_type           varchar(30) NULL,
            biz_data           mediumtext NULL,
            session_id         varchar(36) NULL,
            intent             varchar(40) NULL,
            intent_confidence  decimal(5, 4) NULL,
            sentiment          varchar(20) NULL,
            urgency            varchar(20) NULL,
            risk_level         varchar(20) NULL,
            run_id             varchar(64) NULL,
            trace_id           varchar(64) NULL,
            source_refs        json NULL,
            image_asset_id     varchar(64) NULL,
            image_snapshot_json json NULL,
            selected_visual_subject_json json NULL,
            latency_ms         int NULL,
            unresolved_count   int DEFAULT 0 NOT NULL,
            queue_name         varchar(64) NULL,
            KEY idx_agent_message_user (user_id, message_id),
            KEY idx_agent_message_session (session_id, message_id),
            UNIQUE KEY uk_agent_message_run (run_id),
            KEY idx_agent_message_quality (intent, sentiment, send_time),
            KEY idx_agent_message_image_asset (image_asset_id, message_id)
        ) COLLATE = utf8mb4_general_ci ROW_FORMAT = DYNAMIC
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_run
        (
            run_id                 varchar(64) NOT NULL PRIMARY KEY,
            message_id             int NULL,
            user_id                varchar(32) NOT NULL,
            session_id             varchar(36) NULL,
            otel_trace_id          char(32) NULL,
            agent_id               varchar(64) NOT NULL DEFAULT 'supervisor',
            agent_version          varchar(32) NOT NULL DEFAULT 'v1',
            parent_run_id          varchar(64) NULL,
            handoff_id             varchar(64) NULL,
            actor_type             varchar(16) NOT NULL DEFAULT 'USER',
            status                 varchar(20) NOT NULL DEFAULT 'QUEUED',
            outcome                varchar(32) NULL,
            scenario               varchar(40) NULL,
            intent                 varchar(40) NULL,
            queue_name             varchar(64) NULL,
            model_name             varchar(128) NULL,
            version_json           json NULL,
            experiment_json        json NULL,
            input_tokens           int NOT NULL DEFAULT 0,
            output_tokens          int NOT NULL DEFAULT 0,
            cost_cny               decimal(14, 8) NOT NULL DEFAULT 0,
            latency_ms             int NULL,
            ttft_ms                int NULL,
            pilot_batch_id         varchar(64) NULL,
            evidence_source        varchar(16) NULL,
            quality_json           json NULL,
            reward_signals_json    json NULL,
            capture_level          varchar(16) NOT NULL DEFAULT 'FULL',
            dataset_eligible       varchar(16) NOT NULL DEFAULT 'UNREVIEWED',
            dataset_reviewed_by    varchar(100) NULL,
            dataset_reviewed_at    datetime(3) NULL,
            dataset_review_note    varchar(1000) NULL,
            started_at             datetime(3) NOT NULL,
            completed_at           datetime(3) NULL,
            created_at             datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            updated_at             datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ON UPDATE CURRENT_TIMESTAMP(3),
            UNIQUE KEY uk_agent_run_message (message_id),
            KEY idx_agent_run_trace (otel_trace_id),
            KEY idx_agent_run_status_time (status, started_at),
            KEY idx_agent_run_user_time (user_id, started_at),
            KEY idx_agent_run_agent_time (agent_id, started_at),
            KEY idx_agent_run_parent_time (parent_run_id, started_at)
        ) COMMENT 'durable application-level Agent episode' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_step
        (
            step_id        bigint AUTO_INCREMENT PRIMARY KEY,
            run_id         varchar(64) NOT NULL,
            event_type     varchar(40) NOT NULL,
            node_name      varchar(64) NULL,
            round_no       smallint NULL,
            status         varchar(20) NOT NULL,
            span_id        char(16) NULL,
            input_json     json NULL,
            output_json    json NULL,
            model_name     varchar(128) NULL,
            tool_name      varchar(64) NULL,
            call_id        varchar(128) NULL,
            error_code     varchar(64) NULL,
            error_message  varchar(512) NULL,
            latency_ms     int NULL,
            occurred_at    datetime(3) NOT NULL,
            agent_id       varchar(64) NULL,
            artifact_type  varchar(64) NULL,
            handoff_id     varchar(64) NULL,
            KEY idx_agent_step_run_time (run_id, occurred_at, step_id),
            KEY idx_agent_step_type_status (event_type, status, occurred_at)
        ) COMMENT 'sanitized observable Agent decisions and actions' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_pilot_batch
        (
            batch_id              varchar(64) NOT NULL PRIMARY KEY,
            name                  varchar(120) NOT NULL,
            description           varchar(1000) NULL,
            evidence_source       varchar(16) NOT NULL,
            status                varchar(16) NOT NULL DEFAULT 'DRAFT',
            consent_text_version  varchar(64) NOT NULL,
            created_by            varchar(100) NOT NULL,
            started_at            datetime(3) NULL,
            closed_at             datetime(3) NULL,
            created_at            datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            updated_at            datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ON UPDATE CURRENT_TIMESTAMP(3),
            KEY idx_agent_pilot_batch_status (status, created_at),
            KEY idx_agent_pilot_batch_source (evidence_source, created_at)
        ) COMMENT 'governed AI pilot and evidence batch' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_pilot_participant
        (
            participant_id        varchar(64) NOT NULL PRIMARY KEY,
            batch_id              varchar(64) NOT NULL,
            pseudonym             varchar(64) NOT NULL,
            user_id_hash          char(64) NOT NULL,
            user_id_encrypted     varbinary(512) NULL,
            consented_at          datetime(3) NOT NULL,
            withdrawn_at          datetime(3) NULL,
            status                varchar(16) NOT NULL DEFAULT 'ACTIVE',
            created_by            varchar(100) NOT NULL,
            created_at            datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            updated_at            datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ON UPDATE CURRENT_TIMESTAMP(3),
            UNIQUE KEY uk_agent_pilot_participant_user (batch_id, user_id_hash),
            UNIQUE KEY uk_agent_pilot_participant_alias (batch_id, pseudonym),
            KEY idx_agent_pilot_participant_status (batch_id, status, created_at)
        ) COMMENT 'consented participant with batch-scoped pseudonym' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_privacy_job
        (
            job_id                 varchar(64) NOT NULL PRIMARY KEY,
            user_id                varchar(32) NOT NULL,
            job_type               varchar(16) NOT NULL,
            idempotency_key        varchar(128) NOT NULL,
            request_fingerprint    char(64) NOT NULL,
            status                 varchar(24) NOT NULL DEFAULT 'PENDING',
            current_step           varchar(64) NULL,
            steps_json             json NOT NULL,
            retry_count            int NOT NULL DEFAULT 0,
            error_code             varchar(64) NULL,
            error_message          varchar(512) NULL,
            export_path            varchar(512) NULL,
            export_token_hash      char(64) NULL,
            export_expires_at      datetime(3) NULL,
            requested_at           datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            started_at             datetime(3) NULL,
            completed_at           datetime(3) NULL,
            updated_at             datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ON UPDATE CURRENT_TIMESTAMP(3),
            UNIQUE KEY uk_user_privacy_job_idempotency
                (user_id, job_type, idempotency_key),
            KEY idx_user_privacy_job_user_time (user_id, requested_at),
            KEY idx_user_privacy_job_status (status, updated_at)
        ) COMMENT 'resumable user AI data export and deletion job' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_session_memory
        (
            user_id varchar(32) NOT NULL PRIMARY KEY,
            summary_json json NULL,
            state_json json NULL,
            turn_count int DEFAULT 0 NOT NULL,
            history_cleared_through_message_id bigint DEFAULT 0 NOT NULL,
            updated_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL
                ON UPDATE CURRENT_TIMESTAMP
        ) CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_order_selection
        (
            selection_id          varchar(64) NOT NULL PRIMARY KEY,
            user_id               varchar(32) NOT NULL,
            source_message_id     bigint NULL,
            intent                varchar(40) NOT NULL,
            original_text         varchar(4000) NOT NULL,
            candidates_json       json NOT NULL,
            context_json          json NULL,
            status                varchar(16) NOT NULL DEFAULT 'ACTIVE',
            expires_at            datetime NOT NULL,
            selected_target_type  varchar(32) NULL,
            selected_target_id    varchar(128) NULL,
            selected_message_id   bigint NULL,
            created_at            datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at            datetime NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
            KEY idx_agent_selection_user_status (user_id, status, expires_at),
            UNIQUE KEY uk_agent_selection_message (selected_message_id)
        ) CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_shopping_profile
        (
            user_id varchar(32) NOT NULL PRIMARY KEY,
            profile_json json NULL,
            revision bigint NOT NULL DEFAULT 0,
            updated_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL
                ON UPDATE CURRENT_TIMESTAMP
        ) COMMENT 'durable shopping preferences (budget/brand/scenario)'
          CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_visual_selection
        (
            selection_id          varchar(64) NOT NULL PRIMARY KEY,
            user_id               varchar(32) NOT NULL,
            source_message_id     bigint NOT NULL,
            image_asset_id        varchar(64) NOT NULL,
            original_text         varchar(4000) NOT NULL,
            subjects_json         json NOT NULL,
            constraints_json      json NULL,
            status                varchar(16) NOT NULL DEFAULT 'ACTIVE',
            expires_at            datetime NOT NULL,
            selected_subject_id   varchar(64) NULL,
            selected_message_id   bigint NULL,
            created_at            datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at            datetime NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
            KEY idx_visual_selection_user_status (user_id, status, expires_at),
            KEY idx_visual_selection_source_message (source_message_id),
            UNIQUE KEY uk_visual_selection_message (selected_message_id)
        ) COMMENT 'server-owned visual subject selection' CHARSET = utf8mb4
          COLLATE = utf8mb4_general_ci
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_pending_action
        (
            action_token varchar(80) NOT NULL PRIMARY KEY,
            user_id varchar(32) NOT NULL,
            action_type varchar(64) NOT NULL,
            message_id bigint NULL,
            run_id varchar(64) NULL,
            params_json json NOT NULL,
            business_key varchar(255) NOT NULL,
            args_fingerprint char(64) NOT NULL,
            summary varchar(512) NULL,
            confirm_text varchar(64) NULL,
            risk_tip varchar(512) NULL,
            status varchar(16) NOT NULL,
            result_message text NULL,
            error_message text NULL,
            reconcile_attempts int DEFAULT 0 NOT NULL,
            reconcile_deadline datetime NULL,
            last_reconcile_at datetime NULL,
            review_reason varchar(512) NULL,
            expires_at datetime NOT NULL,
            created_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL
                ON UPDATE CURRENT_TIMESTAMP,
            active_business_key varchar(255) GENERATED ALWAYS AS (
                CASE WHEN status IN ('PENDING','EXECUTING','INCONCLUSIVE','MANUAL_REVIEW')
                     THEN business_key ELSE NULL END
            ) STORED,
            KEY idx_agent_pending_user (user_id, status, expires_at),
            KEY idx_agent_pending_expire (status, expires_at),
            KEY idx_agent_pending_run (run_id, created_at),
            UNIQUE KEY uk_agent_pending_active_business (active_business_key)
        ) CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_recommendation_event
        (
            event_id bigint AUTO_INCREMENT PRIMARY KEY,
            user_id varchar(32) NOT NULL,
            request_id varchar(128) NOT NULL,
            product_id varchar(64) NOT NULL,
            position smallint unsigned NOT NULL,
            source varchar(40) NOT NULL,
            retrieval_mode varchar(20) NOT NULL DEFAULT 'text',
            match_type varchar(32) NULL,
            subject_label varchar(128) NULL,
            recall_source varchar(128) NULL,
            model_version varchar(128) NULL,
            run_id varchar(64) NULL,
            event_type varchar(16) NOT NULL,
            occurred_at datetime(3) NOT NULL,
            created_at datetime(3) DEFAULT CURRENT_TIMESTAMP(3) NOT NULL,
            CONSTRAINT uk_agent_rec_event
                UNIQUE (request_id, product_id, position, event_type),
            KEY idx_agent_rec_user_time (user_id, occurred_at),
            KEY idx_agent_rec_request_type (request_id, event_type)
        ) COMMENT 'auditable recommendation impression and click facts'
          CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS support_session
        (
            session_id varchar(36) NOT NULL PRIMARY KEY,
            user_id varchar(15) NOT NULL,
            status varchar(16) NOT NULL,
            trigger_reason varchar(64) NULL,
            summary text NULL,
            context_json json NULL,
            intent varchar(40) NULL,
            sentiment varchar(20) NULL,
            urgency varchar(20) NULL,
            risk_level varchar(20) NULL,
            assigned_admin varchar(100) NULL,
            source_message_id int NULL,
            created_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL,
            assigned_at datetime NULL,
            resolved_at datetime NULL,
            updated_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL
                ON UPDATE CURRENT_TIMESTAMP,
            -- P0-6: 每用户同时只允许一个活跃会话，由数据库保证而不是应用层先查再插。
            -- 生成列：QUEUED/ASSIGNED/ACTIVE 时为 user_id，其余状态为 NULL；
            -- NULL 不参与唯一索引，所以已解决/已取消的会话不占坑。
            active_user varchar(15) GENERATED ALWAYS AS (
                CASE WHEN status IN ('QUEUED','ASSIGNED','ACTIVE') THEN user_id ELSE NULL END
            ) STORED,
            KEY idx_support_queue (status, urgency, created_at),
            KEY idx_support_user (user_id, status, updated_at),
            UNIQUE KEY uk_support_active_user (active_user)
        ) COMMENT 'human support session' CHARSET = utf8mb4
        """
    )
    # P0-6 对已存在的库补列与唯一索引（CREATE TABLE IF NOT EXISTS 不会动老表）。
    # 全程用 Python 层 information_schema 判断做幂等，不用 SET/PREPARE 多语句
    # （pymysql 默认不开 MULTI_STATEMENTS，多语句会直接报错）。
    bind = op.get_bind()
    from sqlalchemy import text

    has_active_col = bind.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'support_session'
              AND column_name = 'active_user'
            """
        )
    ).scalar()
    if not has_active_col:
        op.execute(
            """
            ALTER TABLE support_session
                ADD COLUMN active_user varchar(15) GENERATED ALWAYS AS (
                    CASE WHEN status IN ('QUEUED','ASSIGNED','ACTIVE') THEN user_id ELSE NULL END
                ) STORED
            """
        )
    has_context_col = bind.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'support_session'
              AND column_name = 'context_json'
            """
        )
    ).scalar()
    if not has_context_col:
        op.execute(
            "ALTER TABLE support_session ADD COLUMN context_json json NULL AFTER summary"
        )
    # 历史数据里可能已有同一用户的多个活跃会话。优先保留已经 ACTIVE、
    # 其次 ASSIGNED、最后 QUEUED；同状态才取最早创建的一条。只按创建时间
    # 会取消正在由客服处理的新会话，反而保留无人认领的旧排队会话。
    # 把其余会话的消息和 Agent 记录合并到主会话，再置为 CANCELLED。
    # 绝不能 DELETE 会话：support_message 没有级联外键，直接删除会留下孤儿记录。
    op.execute("DROP TEMPORARY TABLE IF EXISTS tmp_support_session_merge")
    op.execute(
        """
        CREATE TEMPORARY TABLE tmp_support_session_merge AS
        SELECT user_id,
               SUBSTRING_INDEX(
                   GROUP_CONCAT(
                       session_id
                       ORDER BY FIELD(status, 'ACTIVE', 'ASSIGNED', 'QUEUED'),
                                created_at, session_id
                       SEPARATOR ','
                   ),
                   ',', 1
               ) AS canonical_session_id
        FROM support_session
        WHERE status IN ('QUEUED','ASSIGNED','ACTIVE')
        GROUP BY user_id
        HAVING COUNT(*) > 1
        """
    )
    has_support_message = bind.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_name = 'support_message'
            """
        )
    ).scalar()
    if has_support_message:
        op.execute(
            """
            UPDATE support_message m
            JOIN support_session duplicate_session
              ON duplicate_session.session_id COLLATE utf8mb4_general_ci
                 = m.session_id COLLATE utf8mb4_general_ci
            JOIN tmp_support_session_merge merge_plan
              ON merge_plan.user_id COLLATE utf8mb4_general_ci
                 = duplicate_session.user_id COLLATE utf8mb4_general_ci
            SET m.session_id = merge_plan.canonical_session_id
            WHERE duplicate_session.status IN ('QUEUED','ASSIGNED','ACTIVE')
              AND duplicate_session.session_id COLLATE utf8mb4_general_ci
                  <> merge_plan.canonical_session_id COLLATE utf8mb4_general_ci
            """
        )
    has_agent_message_session = bind.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'agent_message'
              AND column_name = 'session_id'
            """
        )
    ).scalar()
    if has_agent_message_session:
        op.execute(
            """
            UPDATE agent_message m
            JOIN support_session duplicate_session
              ON duplicate_session.session_id COLLATE utf8mb4_general_ci
                 = m.session_id COLLATE utf8mb4_general_ci
            JOIN tmp_support_session_merge merge_plan
              ON merge_plan.user_id COLLATE utf8mb4_general_ci
                 = duplicate_session.user_id COLLATE utf8mb4_general_ci
            SET m.session_id = merge_plan.canonical_session_id
            WHERE duplicate_session.status IN ('QUEUED','ASSIGNED','ACTIVE')
              AND duplicate_session.session_id COLLATE utf8mb4_general_ci
                  <> merge_plan.canonical_session_id COLLATE utf8mb4_general_ci
            """
        )
    op.execute(
        """
        UPDATE support_session duplicate_session
        JOIN tmp_support_session_merge merge_plan
          ON merge_plan.user_id COLLATE utf8mb4_general_ci
             = duplicate_session.user_id COLLATE utf8mb4_general_ci
        SET duplicate_session.status = 'CANCELLED',
            duplicate_session.summary = CONCAT_WS(
                '；', NULLIF(duplicate_session.summary, ''),
                CONCAT('迁移时合并至会话 ', merge_plan.canonical_session_id)
            ),
            duplicate_session.updated_at = NOW()
        WHERE duplicate_session.status IN ('QUEUED','ASSIGNED','ACTIVE')
          AND duplicate_session.session_id COLLATE utf8mb4_general_ci
              <> merge_plan.canonical_session_id COLLATE utf8mb4_general_ci
        """
    )
    op.execute("DROP TEMPORARY TABLE IF EXISTS tmp_support_session_merge")
    has_active_idx = bind.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = 'support_session'
              AND index_name = 'uk_support_active_user'
            """
        )
    ).scalar()
    if not has_active_idx:
        op.execute(
            """
            ALTER TABLE support_session
                ADD UNIQUE KEY uk_support_active_user (active_user)
            """
        )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS support_message
        (
            support_message_id bigint AUTO_INCREMENT PRIMARY KEY,
            session_id varchar(36) NOT NULL,
            sender_type varchar(16) NOT NULL,
            sender_id varchar(100) NULL,
            content text NOT NULL,
            source_message_id int NULL,
            trace_id varchar(64) NULL,
            created_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL,
            KEY idx_support_message_session (session_id, support_message_id)
        ) COMMENT 'human support transcript' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_task
        (
            task_id bigint AUTO_INCREMENT PRIMARY KEY,
            message_id int NOT NULL,
            user_id varchar(15) NOT NULL,
            queue_name varchar(64) NOT NULL,
            priority tinyint NOT NULL,
            status varchar(16) NOT NULL,
            retry_count int DEFAULT 0 NOT NULL,
            deadline_at datetime NULL,
            payload_json json NOT NULL,
            error_message varchar(512) NULL,
            -- P0-2b：任务租约。lease_owner 持有期内其他 Worker 不得接管，
            -- 防止 MQ 重投/Worker 卡顿导致的双执行；租约过期才允许接管。
            lease_owner varchar(64) NULL,
            lease_until datetime NULL,
            -- P0-2a：退避重试。失败后不立即重发，next_retry_at 到了才被恢复扫描拉起。
            next_retry_at datetime NULL,
            created_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL
                ON UPDATE CURRENT_TIMESTAMP,
            started_at datetime NULL,
            completed_at datetime NULL,
            CONSTRAINT uk_agent_task_message UNIQUE (message_id),
            KEY idx_agent_task_dispatch (status, priority, created_at)
        ) COMMENT 'durable Agent task ledger' CHARSET = utf8mb4
        """
    )
    # P0-2 对已存在的库补租约/退避列（幂等，见 support_session 的同样处理）。
    bind = op.get_bind()
    from sqlalchemy import text

    for column, ddl in (
        ("lease_owner", "ALTER TABLE agent_task ADD COLUMN lease_owner varchar(64) NULL"),
        ("lease_until", "ALTER TABLE agent_task ADD COLUMN lease_until datetime NULL"),
        ("next_retry_at", "ALTER TABLE agent_task ADD COLUMN next_retry_at datetime NULL"),
    ):
        has_col = bind.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = 'agent_task'
                  AND column_name = :col
                """
            ),
            {"col": column},
        ).scalar()
        if not has_col:
            op.execute(ddl)
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_message_feedback
        (
            feedback_id bigint AUTO_INCREMENT PRIMARY KEY,
            message_id int NOT NULL,
            user_id varchar(15) NOT NULL,
            rating tinyint NOT NULL,
            reason varchar(64) NULL,
            detail varchar(500) NULL,
            created_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL
                ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT uk_agent_feedback UNIQUE (message_id, user_id)
        ) COMMENT 'user feedback for Agent answers' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_badcase_candidate
        (
            candidate_id bigint AUTO_INCREMENT PRIMARY KEY,
            message_id int NULL,
            run_id varchar(64) NULL,
            candidate_type varchar(32) NOT NULL,
            reason varchar(255) NOT NULL,
            status varchar(24) DEFAULT 'NEW' NOT NULL,
            source varchar(32) DEFAULT 'SYSTEM' NOT NULL,
            severity varchar(16) DEFAULT 'MEDIUM' NOT NULL,
            snapshot_json json NULL,
            labels_json json NULL,
            judge_json json NULL,
            owner varchar(100) NULL,
            fix_version varchar(64) NULL,
            regression_case_id bigint NULL,
            occurrence_count int DEFAULT 1 NOT NULL,
            first_seen_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL,
            reviewer varchar(100) NULL,
            review_remark varchar(500) NULL,
            created_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL
                ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT uk_badcase_message_type UNIQUE (message_id, candidate_type),
            KEY idx_badcase_status (status, created_at),
            KEY idx_badcase_run (run_id, created_at)
        ) COMMENT 'Agent badcase review pool' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_regression_case
        (
            case_id bigint AUTO_INCREMENT PRIMARY KEY,
            candidate_id bigint NULL,
            case_key varchar(128) NOT NULL,
            name varchar(255) NOT NULL,
            scenario varchar(40) NULL,
            input_json json NOT NULL,
            expected_json json NOT NULL,
            status varchar(16) DEFAULT 'ACTIVE' NOT NULL,
            created_by varchar(100) NOT NULL,
            last_result varchar(16) NULL,
            last_run_at datetime NULL,
            created_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL
                ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT uk_agent_regression_case_key UNIQUE (case_key),
            KEY idx_agent_regression_status (status, updated_at)
        ) COMMENT 'human-reviewed Agent regression cases' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS support_case
        (
            case_id             bigint AUTO_INCREMENT PRIMARY KEY,
            case_no             varchar(40) NOT NULL,
            user_id             varchar(32) NOT NULL,
            order_id            varchar(64) NULL,
            order_item_id       varchar(64) NULL,
            category            varchar(32) NOT NULL,
            status              varchar(20) NOT NULL DEFAULT 'OPEN',
            description         varchar(4000) NOT NULL,
            evidence_json       json NULL,
            source_message_id   bigint NULL,
            run_id              varchar(64) NULL,
            action_token        varchar(80) NULL,
            idempotency_key     varchar(128) NULL,
            priority            varchar(16) NOT NULL DEFAULT 'NORMAL',
            forced_handoff      tinyint(1) NOT NULL DEFAULT 0,
            support_session_id  varchar(36) NULL,
            assigned_admin      varchar(100) NULL,
            resolution_code     varchar(64) NULL,
            root_cause          varchar(255) NULL,
            resolution_summary  varchar(2000) NULL,
            created_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            updated_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ON UPDATE CURRENT_TIMESTAMP(3),
            resolved_at         datetime(3) NULL,
            UNIQUE KEY uk_support_case_no (case_no),
            UNIQUE KEY uk_support_case_idempotency (user_id, idempotency_key),
            KEY idx_support_case_user_time (user_id, created_at),
            KEY idx_support_case_status_time (status, priority, created_at),
            KEY idx_support_case_run (run_id, created_at)
        ) COMMENT '独立售后业务工单' CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci
        """
    )

    # Agentic Commerce v2 keeps its decision artifacts in the Agent domain.
    # Product/order/stock remain authoritative through authenticated internal
    # interfaces; these tables retain only the auditable decision projection.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_shopping_mission
        (
            user_id             varchar(32) NOT NULL PRIMARY KEY,
            mission_id          varchar(64) NOT NULL,
            status              varchar(20) NOT NULL DEFAULT 'ACTIVE',
            mission_json        json NOT NULL,
            source_message_id   bigint NULL,
            revision            bigint NOT NULL DEFAULT 1,
            expires_at          datetime(3) NOT NULL,
            created_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            updated_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ON UPDATE CURRENT_TIMESTAMP(3),
            UNIQUE KEY uk_agent_shopping_mission_id (mission_id),
            KEY idx_agent_shopping_mission_active (status, expires_at)
        ) COMMENT 'current structured shopping mission per user' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_category_need_schema
        (
            schema_key          varchar(64) NOT NULL,
            version             varchar(32) NOT NULL,
            status              varchar(16) NOT NULL DEFAULT 'PUBLISHED',
            schema_json         json NOT NULL,
            created_by          varchar(100) NULL,
            created_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            updated_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ON UPDATE CURRENT_TIMESTAMP(3),
            PRIMARY KEY (schema_key, version),
            KEY idx_category_need_schema_status (status, schema_key)
        ) COMMENT 'versioned category shopping-decision schemas' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_product_decision_feature
        (
            feature_id          bigint AUTO_INCREMENT PRIMARY KEY,
            product_id          varchar(64) NOT NULL,
            feature_key         varchar(64) NOT NULL,
            feature_value       varchar(255) NOT NULL,
            source_type         varchar(32) NOT NULL,
            evidence_json       json NULL,
            confidence          decimal(5,4) NOT NULL DEFAULT 0,
            review_status       varchar(16) NOT NULL DEFAULT 'DRAFT',
            version             varchar(32) NOT NULL DEFAULT 'v1',
            valid_from          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            valid_until         datetime(3) NULL,
            reviewed_by         varchar(100) NULL,
            reviewed_at         datetime(3) NULL,
            created_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            updated_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ON UPDATE CURRENT_TIMESTAMP(3),
            UNIQUE KEY uk_agent_product_feature_version
                (product_id, feature_key, feature_value, version),
            KEY idx_agent_product_feature_lookup
                (product_id, review_status, valid_until)
        ) COMMENT 'auditable product decision feature projection' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_final_offer_snapshot
        (
            snapshot_id         varchar(64) NOT NULL PRIMARY KEY,
            user_id             varchar(32) NOT NULL,
            product_id          varchar(64) NOT NULL,
            sku_key             varchar(64) NOT NULL,
            offer_json          json NOT NULL,
            expires_at          datetime(3) NOT NULL,
            created_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            KEY idx_agent_offer_user_expiry (user_id, expires_at),
            KEY idx_agent_offer_product_expiry (product_id, expires_at)
        ) COMMENT 'user-bound verified single SKU offer snapshots' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_ranking_policy_decision
        (
            decision_id         varchar(64) NOT NULL PRIMARY KEY,
            request_id          varchar(128) NOT NULL,
            mission_id          varchar(64) NULL,
            user_id             varchar(32) NOT NULL,
            policy_version      varchar(32) NOT NULL,
            decision_json       json NOT NULL,
            created_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            UNIQUE KEY uk_agent_ranking_request (request_id),
            KEY idx_agent_ranking_mission (mission_id, created_at),
            KEY idx_agent_ranking_user_time (user_id, created_at)
        ) COMMENT 'auditable relevance and operations ranking decision' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_recommendation_explanation
        (
            explanation_id      bigint AUTO_INCREMENT PRIMARY KEY,
            decision_id         varchar(64) NOT NULL,
            product_id          varchar(64) NOT NULL,
            position            smallint unsigned NOT NULL,
            explanation_json    json NOT NULL,
            created_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            UNIQUE KEY uk_agent_rec_explanation (decision_id, product_id),
            KEY idx_agent_rec_explanation_position (decision_id, position)
        ) COMMENT 'evidence-backed recommendation explanations' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS commerce_outcome_ledger
        (
            ledger_id           bigint AUTO_INCREMENT PRIMARY KEY,
            event_id            varchar(128) NOT NULL,
            source              varchar(32) NOT NULL,
            idempotency_key     varchar(160) NOT NULL,
            event_type          varchar(32) NOT NULL,
            user_id             varchar(32) NOT NULL,
            request_id          varchar(128) NULL,
            run_id              varchar(64) NULL,
            pilot_batch_id      varchar(64) NULL,
            product_id          varchar(64) NULL,
            sku_key             varchar(64) NULL,
            order_id            varchar(64) NULL,
            payload_json        json NULL,
            occurred_at         datetime(3) NOT NULL,
            created_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            UNIQUE KEY uk_commerce_outcome_source_key (source, idempotency_key),
            UNIQUE KEY uk_commerce_outcome_event (event_id),
            KEY idx_commerce_outcome_request_time (request_id, occurred_at),
            KEY idx_commerce_outcome_user_time (user_id, occurred_at),
            KEY idx_commerce_outcome_product_time (product_id, occurred_at)
        ) COMMENT 'immutable recommendation and commerce outcome ledger' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_after_sales_policy
        (
            policy_id           varchar(64) NOT NULL,
            version             varchar(32) NOT NULL,
            status              varchar(16) NOT NULL DEFAULT 'DRAFT',
            priority            int NOT NULL DEFAULT 0,
            scope_json          json NOT NULL,
            rule_json           json NOT NULL,
            effective_start     datetime(3) NOT NULL,
            effective_end       datetime(3) NULL,
            created_by          varchar(100) NULL,
            created_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            updated_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ON UPDATE CURRENT_TIMESTAMP(3),
            PRIMARY KEY (policy_id, version),
            KEY idx_after_sales_policy_effective
                (status, effective_start, effective_end, priority)
        ) COMMENT 'versioned declarative after-sales eligibility rules' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_after_sales_eligibility
        (
            decision_id         varchar(64) NOT NULL PRIMARY KEY,
            user_id             varchar(32) NOT NULL,
            order_id            varchar(64) NULL,
            order_item_id       varchar(64) NULL,
            decision_json       json NOT NULL,
            expires_at          datetime(3) NOT NULL,
            created_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            KEY idx_after_sales_eligibility_order (order_id, order_item_id),
            KEY idx_after_sales_eligibility_user_expiry (user_id, expires_at)
        ) COMMENT 'cached deterministic after-sales eligibility decisions' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        INSERT IGNORE INTO agent_after_sales_policy
            (policy_id, version, status, priority, scope_json, rule_json,
             effective_start, effective_end, created_by, created_at, updated_at)
        VALUES
            ('system-refund-state', 'v1', 'PUBLISHED', 0,
             JSON_OBJECT('scopeType', 'GLOBAL'),
             JSON_OBJECT(
                 'action', 'REFUND',
                 'orderStatuses', JSON_ARRAY(1, 2, 7),
                 'itemStatuses', JSON_ARRAY(1),
                 'requiredEvidence', JSON_ARRAY()
             ),
             '2020-01-01 00:00:00.000', NULL, 'SYSTEM_MIGRATION', NOW(3), NOW(3))
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_inventory_supply_parameter
        (
            product_id          varchar(64) NOT NULL,
            sku_key             varchar(64) NOT NULL,
            supplier_ref        varchar(100) NULL,
            lead_time_days      int NOT NULL DEFAULT 7,
            safety_stock        int NOT NULL DEFAULT 0,
            min_order_quantity  int NOT NULL DEFAULT 1,
            review_period_days  int NOT NULL DEFAULT 14,
            enabled             tinyint(1) NOT NULL DEFAULT 1,
            updated_by          varchar(100) NULL,
            updated_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ON UPDATE CURRENT_TIMESTAMP(3),
            PRIMARY KEY (product_id, sku_key)
        ) COMMENT 'manual inventory planning inputs; never a purchase order' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_inventory_inbound
        (
            inbound_id          varchar(64) NOT NULL PRIMARY KEY,
            product_id          varchar(64) NOT NULL,
            sku_key             varchar(64) NOT NULL,
            quantity            int NOT NULL,
            eta_date            date NULL,
            status              varchar(16) NOT NULL DEFAULT 'PLANNED',
            source_ref          varchar(100) NULL,
            updated_at          datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ON UPDATE CURRENT_TIMESTAMP(3),
            KEY idx_inventory_inbound_sku (product_id, sku_key, status, eta_date)
        ) COMMENT 'declared inbound stock used for planning only' CHARSET = utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_inventory_forecast
        (
            forecast_id         varchar(64) NOT NULL PRIMARY KEY,
            product_id          varchar(64) NOT NULL,
            sku_key             varchar(64) NOT NULL,
            forecast_json       json NOT NULL,
            status              varchar(16) NOT NULL DEFAULT 'OPEN',
            generated_at        datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            reviewed_by         varchar(100) NULL,
            reviewed_at         datetime(3) NULL,
            KEY idx_inventory_forecast_status (status, generated_at),
            KEY idx_inventory_forecast_sku (product_id, sku_key, generated_at)
        ) COMMENT 'manual-only inventory replenishment suggestions' CHARSET = utf8mb4
        """
    )

    _reconcile_agent_message()
    _reconcile_session_memory()
    _reconcile_episode_tables()
    _reconcile_evidence_and_privacy_tables()
    _reconcile_shopping_profile()
    _reconcile_pending_action()
    _reconcile_recommendation_event()
    _reconcile_support_case()
    _reconcile_quality_tables()
    _reconcile_indexes()


def _reconcile_agent_message() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_message")}
    definitions = {
        "session_id": sa.Column("session_id", sa.String(36), nullable=True),
        "intent": sa.Column("intent", sa.String(40), nullable=True),
        "intent_confidence": sa.Column(
            "intent_confidence", sa.Numeric(5, 4), nullable=True
        ),
        "sentiment": sa.Column("sentiment", sa.String(20), nullable=True),
        "urgency": sa.Column("urgency", sa.String(20), nullable=True),
        "risk_level": sa.Column("risk_level", sa.String(20), nullable=True),
        "run_id": sa.Column("run_id", sa.String(64), nullable=True),
        "trace_id": sa.Column("trace_id", sa.String(64), nullable=True),
        "source_refs": sa.Column("source_refs", sa.JSON(), nullable=True),
        "latency_ms": sa.Column("latency_ms", sa.Integer(), nullable=True),
        "unresolved_count": sa.Column(
            "unresolved_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        "queue_name": sa.Column("queue_name", sa.String(64), nullable=True),
        "image_asset_id": sa.Column("image_asset_id", sa.String(64), nullable=True),
        "image_snapshot_json": sa.Column("image_snapshot_json", sa.JSON(), nullable=True),
        "selected_visual_subject_json": sa.Column(
            "selected_visual_subject_json", sa.JSON(), nullable=True
        ),
    }
    for name, column in definitions.items():
        if name not in columns:
            op.add_column("agent_message", column)

    op.execute(
        "ALTER TABLE agent_message MODIFY COLUMN user_message varchar(4000) NULL"
    )
    op.execute("ALTER TABLE agent_message MODIFY COLUMN biz_data mediumtext NULL")


def _reconcile_session_memory() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("agent_session_memory")
    }
    if "history_cleared_through_message_id" not in columns:
        op.execute(
            "ALTER TABLE agent_session_memory "
            "ADD COLUMN history_cleared_through_message_id "
            "bigint NOT NULL DEFAULT 0 AFTER turn_count"
        )
    op.execute(
        """
        UPDATE agent_session_memory
        SET history_cleared_through_message_id=GREATEST(
            history_cleared_through_message_id,
            COALESCE(
                CAST(JSON_UNQUOTE(JSON_EXTRACT(
                    state_json,
                    '$.historyClearedThroughMessageId'
                )) AS UNSIGNED),
                0
            )
        )
        """
    )


def _reconcile_episode_tables() -> None:
    """Complete Episode tables created by an interrupted or preview migration."""
    bind = op.get_bind()
    definitions = {
        "agent_run": {
            "message_id": "bigint NULL",
            "user_id": "varchar(32) NULL",
            "session_id": "varchar(36) NULL",
            "otel_trace_id": "char(32) NULL",
            "status": "varchar(20) NOT NULL DEFAULT 'QUEUED'",
            "outcome": "varchar(32) NULL",
            "scenario": "varchar(40) NULL",
            "intent": "varchar(40) NULL",
            "queue_name": "varchar(64) NULL",
            "model_name": "varchar(128) NULL",
            "version_json": "json NULL",
            "experiment_json": "json NULL",
            "input_tokens": "int NOT NULL DEFAULT 0",
            "output_tokens": "int NOT NULL DEFAULT 0",
            "cost_cny": "decimal(14, 8) NOT NULL DEFAULT 0",
            "latency_ms": "int NULL",
            "ttft_ms": "int NULL",
            "pilot_batch_id": "varchar(64) NULL",
            "evidence_source": "varchar(16) NULL",
            "quality_json": "json NULL",
            "reward_signals_json": "json NULL",
            "capture_level": "varchar(16) NOT NULL DEFAULT 'FULL'",
            "dataset_eligible": "varchar(16) NOT NULL DEFAULT 'UNREVIEWED'",
            "dataset_reviewed_by": "varchar(100) NULL",
            "dataset_reviewed_at": "datetime(3) NULL",
            "dataset_review_note": "varchar(1000) NULL",
            "started_at": "datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)",
            "completed_at": "datetime(3) NULL",
            "created_at": "datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)",
            "updated_at": (
                "datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) "
                "ON UPDATE CURRENT_TIMESTAMP(3)"
            ),
            "agent_id": "varchar(64) NOT NULL DEFAULT 'supervisor'",
            "agent_version": "varchar(32) NOT NULL DEFAULT 'v1'",
            "parent_run_id": "varchar(64) NULL",
            "handoff_id": "varchar(64) NULL",
            "actor_type": "varchar(16) NOT NULL DEFAULT 'USER'",
        },
        "agent_step": {
            "run_id": "varchar(64) NULL",
            "event_type": "varchar(40) NOT NULL DEFAULT 'LEGACY_EVENT'",
            "node_name": "varchar(64) NULL",
            "round_no": "smallint NULL",
            "status": "varchar(20) NOT NULL DEFAULT 'OK'",
            "span_id": "char(16) NULL",
            "input_json": "json NULL",
            "output_json": "json NULL",
            "model_name": "varchar(128) NULL",
            "tool_name": "varchar(64) NULL",
            "call_id": "varchar(128) NULL",
            "error_code": "varchar(64) NULL",
            "error_message": "varchar(512) NULL",
            "latency_ms": "int NULL",
            "occurred_at": "datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)",
            "agent_id": "varchar(64) NULL",
            "artifact_type": "varchar(64) NULL",
            "handoff_id": "varchar(64) NULL",
        },
    }
    inspector = sa.inspect(bind)
    for table_name, table_definitions in definitions.items():
        existing = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        for name, ddl in table_definitions.items():
            if name not in existing:
                op.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl}")

    # Preview builds briefly allowed nullable ownership columns. Preserve those rows,
    # but mark their provenance explicitly before enforcing the runtime contract.
    op.execute(
        "UPDATE agent_run SET user_id='<LEGACY>' "
        "WHERE user_id IS NULL OR user_id=''"
    )
    op.execute(
        "ALTER TABLE agent_run MODIFY COLUMN user_id varchar(32) NOT NULL"
    )
    op.execute(
        "UPDATE agent_run SET dataset_eligible='UNREVIEWED', "
        "dataset_reviewed_by=NULL, dataset_reviewed_at=NULL, dataset_review_note=NULL "
        "WHERE dataset_eligible IS NULL OR dataset_eligible NOT IN "
        "('UNREVIEWED','APPROVED','REJECTED')"
    )
    op.execute(
        "ALTER TABLE agent_run MODIFY COLUMN dataset_eligible varchar(16) "
        "NOT NULL DEFAULT 'UNREVIEWED'"
    )
    op.execute(
        "UPDATE agent_run SET evidence_source=NULL "
        "WHERE evidence_source IS NOT NULL AND evidence_source NOT IN "
        "('SYNTHETIC','LOCAL_PILOT','REAL_USER')"
    )
    op.execute(
        "UPDATE agent_step SET run_id=CONCAT('legacy-step-', step_id) "
        "WHERE run_id IS NULL OR run_id=''"
    )
    op.execute(
        "ALTER TABLE agent_step MODIFY COLUMN run_id varchar(64) NOT NULL"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_handoff (
            handoff_id varchar(64) NOT NULL PRIMARY KEY,
            parent_run_id varchar(64) NULL,
            child_run_id varchar(64) NOT NULL,
            source_agent varchar(64) NOT NULL,
            target_agent varchar(64) NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'STARTED',
            input_summary_json json NULL,
            artifact_summary_json json NULL,
            latency_ms int NULL,
            error_code varchar(64) NULL,
            completed_at datetime(3) NULL,
            created_at datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            UNIQUE KEY uk_agent_handoff_child (child_run_id),
            KEY idx_agent_handoff_parent (parent_run_id, created_at),
            KEY idx_agent_handoff_status (status, created_at)
        ) CHARSET=utf8mb4
    """)


def _reconcile_evidence_and_privacy_tables() -> None:
    inspector = sa.inspect(op.get_bind())
    if "commerce_outcome_ledger" in inspector.get_table_names():
        columns = {
            column["name"]
            for column in inspector.get_columns("commerce_outcome_ledger")
        }
        if "pilot_batch_id" not in columns:
            op.execute(
                "ALTER TABLE commerce_outcome_ledger "
                "ADD COLUMN pilot_batch_id varchar(64) NULL AFTER run_id"
            )


def _reconcile_shopping_profile() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("agent_shopping_profile")
    }
    if "revision" not in columns:
        op.execute(
            "ALTER TABLE agent_shopping_profile "
            "ADD COLUMN revision bigint NOT NULL DEFAULT 0 AFTER profile_json"
        )
    op.execute(
        "UPDATE agent_shopping_profile SET revision=0 WHERE revision IS NULL"
    )


def _reconcile_pending_action() -> None:
    """Add business-level dedupe and bounded reconciliation to legacy databases."""
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("agent_pending_action")}
    definitions = {
        "run_id": sa.Column("run_id", sa.String(64), nullable=True),
        "business_key": sa.Column("business_key", sa.String(255), nullable=True),
        "args_fingerprint": sa.Column("args_fingerprint", sa.String(64), nullable=True),
        "reconcile_attempts": sa.Column(
            "reconcile_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        "reconcile_deadline": sa.Column("reconcile_deadline", sa.DateTime(), nullable=True),
        "last_reconcile_at": sa.Column("last_reconcile_at", sa.DateTime(), nullable=True),
        "review_reason": sa.Column("review_reason", sa.String(512), nullable=True),
    }
    for name, column in definitions.items():
        if name not in columns:
            op.add_column("agent_pending_action", column)

    # Old rows did not retain enough information to reconstruct a truthful business
    # identity. Give each one a non-conflicting legacy key instead of guessing from a
    # mutable summary. The JSON hash remains useful for audit, but is not presented as
    # the canonical fingerprint produced by the new application path.
    op.execute(
        """
        UPDATE agent_pending_action
        SET business_key = CONCAT('legacy:', action_token)
        WHERE business_key IS NULL OR business_key = ''
        """
    )
    op.execute(
        """
        UPDATE agent_pending_action
        SET args_fingerprint = LOWER(SHA2(CAST(params_json AS CHAR), 256))
        WHERE args_fingerprint IS NULL OR args_fingerprint = ''
        """
    )
    op.execute(
        "ALTER TABLE agent_pending_action MODIFY COLUMN business_key varchar(255) NOT NULL"
    )
    op.execute(
        "ALTER TABLE agent_pending_action MODIFY COLUMN args_fingerprint char(64) NOT NULL"
    )

    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("agent_pending_action")
    }
    if "active_business_key" not in columns:
        op.execute(
            """
            ALTER TABLE agent_pending_action
            ADD COLUMN active_business_key varchar(255) GENERATED ALWAYS AS (
                CASE WHEN status IN ('PENDING','EXECUTING','INCONCLUSIVE','MANUAL_REVIEW')
                     THEN business_key ELSE NULL END
            ) STORED
            """
        )


def _reconcile_recommendation_event() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(
            "agent_recommendation_event"
        )
    }
    definitions = {
        "retrieval_mode": "varchar(20) NOT NULL DEFAULT 'text'",
        "match_type": "varchar(32) NULL",
        "subject_label": "varchar(128) NULL",
        "recall_source": "varchar(128) NULL",
        "model_version": "varchar(128) NULL",
        "run_id": "varchar(64) NULL",
    }
    for name, ddl in definitions.items():
        if name not in columns:
            op.execute(
                f"ALTER TABLE agent_recommendation_event ADD COLUMN {name} {ddl}"
            )


def _reconcile_support_case() -> None:
    """Complete the independent support-case table on legacy databases."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "support_case" not in inspector.get_table_names():
        return
    definitions = {
        "case_no": "varchar(40) NULL",
        "user_id": "varchar(32) NULL",
        "order_id": "varchar(64) NULL",
        "order_item_id": "varchar(64) NULL",
        "category": "varchar(32) NOT NULL DEFAULT 'OTHER'",
        "status": "varchar(20) NOT NULL DEFAULT 'OPEN'",
        "description": "varchar(4000) NOT NULL DEFAULT ''",
        "evidence_json": "json NULL",
        "source_message_id": "bigint NULL",
        "run_id": "varchar(64) NULL",
        "action_token": "varchar(80) NULL",
        "idempotency_key": "varchar(128) NULL",
        "priority": "varchar(16) NOT NULL DEFAULT 'NORMAL'",
        "forced_handoff": "tinyint(1) NOT NULL DEFAULT 0",
        "support_session_id": "varchar(36) NULL",
        "assigned_admin": "varchar(100) NULL",
        "resolution_code": "varchar(64) NULL",
        "root_cause": "varchar(255) NULL",
        "resolution_summary": "varchar(2000) NULL",
        "created_at": "datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)",
        "updated_at": "datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)",
        "resolved_at": "datetime(3) NULL",
    }
    existing = {column["name"] for column in inspector.get_columns("support_case")}
    for name, ddl in definitions.items():
        if name not in existing:
            op.execute(f"ALTER TABLE support_case ADD COLUMN {name} {ddl}")
    op.execute(
        "UPDATE support_case SET case_no=CONCAT('LEGACY-', case_id) "
        "WHERE case_no IS NULL OR case_no=''"
    )
    op.execute(
        "UPDATE support_case SET user_id='<LEGACY>' "
        "WHERE user_id IS NULL OR user_id=''"
    )
    op.execute("ALTER TABLE support_case MODIFY COLUMN case_no varchar(40) NOT NULL")
    op.execute("ALTER TABLE support_case MODIFY COLUMN user_id varchar(32) NOT NULL")


def _reconcile_quality_tables() -> None:
    """Upgrade the legacy two-state badcase pool without discarding reviews."""
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("ai_badcase_candidate")
    }
    definitions = {
        "run_id": "varchar(64) NULL",
        "source": "varchar(32) NOT NULL DEFAULT 'SYSTEM'",
        "severity": "varchar(16) NOT NULL DEFAULT 'MEDIUM'",
        "labels_json": "json NULL",
        "judge_json": "json NULL",
        "owner": "varchar(100) NULL",
        "fix_version": "varchar(64) NULL",
        "regression_case_id": "bigint NULL",
        "occurrence_count": "int NOT NULL DEFAULT 1",
        "first_seen_at": "datetime NOT NULL DEFAULT CURRENT_TIMESTAMP",
    }
    for name, ddl in definitions.items():
        if name not in columns:
            op.execute(f"ALTER TABLE ai_badcase_candidate ADD COLUMN {name} {ddl}")

    op.execute(
        """
        UPDATE ai_badcase_candidate
        SET status = CASE status
            WHEN 'PENDING' THEN 'NEW'
            WHEN 'RESOLVED' THEN 'CLOSED'
            ELSE status
        END,
        first_seen_at = COALESCE(first_seen_at, created_at, NOW()),
        occurrence_count = GREATEST(COALESCE(occurrence_count, 1), 1)
        """
    )
    op.execute(
        "ALTER TABLE ai_badcase_candidate MODIFY COLUMN status varchar(24) "
        "NOT NULL DEFAULT 'NEW'"
    )


def _reconcile_indexes() -> None:
    indexes = {
        "agent_message": (
            ("idx_agent_message_user", ("user_id", "message_id"), False),
            ("idx_agent_message_session", ("session_id", "message_id"), False),
            ("uk_agent_message_run", ("run_id",), True),
            (
                "idx_agent_message_quality",
                ("intent", "sentiment", "send_time"),
                False,
            ),
            (
                "idx_agent_message_image_asset",
                ("image_asset_id", "message_id"),
                False,
            ),
        ),
        "agent_run": (
            ("uk_agent_run_message", ("message_id",), True),
            ("idx_agent_run_trace", ("otel_trace_id",), False),
            ("idx_agent_run_status_time", ("status", "started_at"), False),
            ("idx_agent_run_user_time", ("user_id", "started_at"), False),
            ("idx_agent_run_agent_time", ("agent_id", "started_at"), False),
            (
                "idx_agent_run_parent_time",
                ("parent_run_id", "started_at"),
                False,
            ),
            (
                "idx_agent_run_pilot_time",
                ("pilot_batch_id", "started_at"),
                False,
            ),
        ),
        "agent_step": (
            ("idx_agent_step_run_time", ("run_id", "occurred_at", "step_id"), False),
            ("idx_agent_step_type_status", ("event_type", "status", "occurred_at"), False),
        ),
        "agent_order_selection": (
            (
                "idx_agent_selection_user_status",
                ("user_id", "status", "expires_at"),
                False,
            ),
            ("uk_agent_selection_message", ("selected_message_id",), True),
        ),
        "agent_visual_selection": (
            (
                "idx_visual_selection_user_status",
                ("user_id", "status", "expires_at"),
                False,
            ),
            (
                "idx_visual_selection_source_message",
                ("source_message_id",),
                False,
            ),
            (
                "uk_visual_selection_message",
                ("selected_message_id",),
                True,
            ),
        ),
        "agent_pending_action": (
            (
                "idx_agent_pending_user",
                ("user_id", "status", "expires_at"),
                False,
            ),
            ("idx_agent_pending_expire", ("status", "expires_at"), False),
            ("idx_agent_pending_run", ("run_id", "created_at"), False),
            (
                "uk_agent_pending_active_business",
                ("active_business_key",),
                True,
            ),
        ),
        "agent_recommendation_event": (
            (
                "uk_agent_rec_event",
                ("request_id", "product_id", "position", "event_type"),
                True,
            ),
            ("idx_agent_rec_user_time", ("user_id", "occurred_at"), False),
            ("idx_agent_rec_request_type", ("request_id", "event_type"), False),
        ),
        "support_session": (
            ("idx_support_queue", ("status", "urgency", "created_at"), False),
            ("idx_support_user", ("user_id", "status", "updated_at"), False),
        ),
        "support_message": (
            (
                "idx_support_message_session",
                ("session_id", "support_message_id"),
                False,
            ),
        ),
        "agent_task": (
            ("uk_agent_task_message", ("message_id",), True),
            (
                "idx_agent_task_dispatch",
                ("status", "priority", "created_at"),
                False,
            ),
        ),
        "agent_message_feedback": (
            ("uk_agent_feedback", ("message_id", "user_id"), True),
        ),
        "ai_badcase_candidate": (
            (
                "uk_badcase_message_type",
                ("message_id", "candidate_type"),
                True,
            ),
            ("idx_badcase_status", ("status", "created_at"), False),
            ("idx_badcase_run", ("run_id", "created_at"), False),
        ),
        "agent_regression_case": (
            ("uk_agent_regression_case_key", ("case_key",), True),
            (
                "idx_agent_regression_status",
                ("status", "updated_at"),
                False,
            ),
        ),
        "support_case": (
            ("uk_support_case_no", ("case_no",), True),
            ("uk_support_case_idempotency", ("user_id", "idempotency_key"), True),
            ("idx_support_case_user_time", ("user_id", "created_at"), False),
            ("idx_support_case_status_time", ("status", "priority", "created_at"), False),
            ("idx_support_case_run", ("run_id", "created_at"), False),
        ),
        "agent_pilot_batch": (
            ("idx_agent_pilot_batch_status", ("status", "created_at"), False),
            ("idx_agent_pilot_batch_source", ("evidence_source", "created_at"), False),
        ),
        "agent_pilot_participant": (
            ("uk_agent_pilot_participant_user", ("batch_id", "user_id_hash"), True),
            ("uk_agent_pilot_participant_alias", ("batch_id", "pseudonym"), True),
            ("idx_agent_pilot_participant_status", ("batch_id", "status", "created_at"), False),
        ),
        "user_privacy_job": (
            ("uk_user_privacy_job_idempotency", ("user_id", "job_type", "idempotency_key"), True),
            ("idx_user_privacy_job_user_time", ("user_id", "requested_at"), False),
            ("idx_user_privacy_job_status", ("status", "updated_at"), False),
        ),
    }
    inspector = sa.inspect(op.get_bind())
    for table_name, definitions in indexes.items():
        existing = {
            item["name"] for item in inspector.get_indexes(table_name) if item.get("name")
        }
        existing.update(
            item["name"]
            for item in inspector.get_unique_constraints(table_name)
            if item.get("name")
        )
        for name, columns, unique in definitions:
            if name not in existing:
                op.create_index(name, table_name, list(columns), unique=unique)


def downgrade() -> None:
    for table_name in (
        "user_privacy_job",
        "agent_pilot_participant",
        "agent_pilot_batch",
        "agent_handoff",
        "agent_step",
        "agent_run",
        "agent_regression_case",
        "support_case",
        "ai_badcase_candidate",
        "agent_message_feedback",
        "agent_task",
        "support_message",
        "support_session",
        "agent_recommendation_event",
        "agent_visual_selection",
        "agent_pending_action",
        "agent_shopping_profile",
        "agent_order_selection",
        "agent_session_memory",
        "agent_message",
    ):
        op.drop_table(table_name)
