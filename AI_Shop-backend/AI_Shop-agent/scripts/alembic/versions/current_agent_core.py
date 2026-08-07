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
            latency_ms         int NULL,
            unresolved_count   int DEFAULT 0 NOT NULL,
            queue_name         varchar(64) NULL,
            KEY idx_agent_message_user (user_id, message_id),
            KEY idx_agent_message_session (session_id, message_id),
            UNIQUE KEY uk_agent_message_run (run_id),
            KEY idx_agent_message_quality (intent, sentiment, send_time)
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
            quality_json           json NULL,
            reward_signals_json    json NULL,
            capture_level          varchar(16) NOT NULL DEFAULT 'FULL',
            dataset_eligible       varchar(16) NOT NULL DEFAULT 'UNREVIEWED',
            started_at             datetime(3) NOT NULL,
            completed_at           datetime(3) NULL,
            created_at             datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            updated_at             datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ON UPDATE CURRENT_TIMESTAMP(3),
            UNIQUE KEY uk_agent_run_message (message_id),
            KEY idx_agent_run_trace (otel_trace_id),
            KEY idx_agent_run_status_time (status, started_at),
            KEY idx_agent_run_user_time (user_id, started_at)
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
            KEY idx_agent_step_run_time (run_id, occurred_at, step_id),
            KEY idx_agent_step_type_status (event_type, status, occurred_at)
        ) COMMENT 'sanitized observable Agent decisions and actions' CHARSET = utf8mb4
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

    _reconcile_agent_message()
    _reconcile_episode_tables()
    _reconcile_shopping_profile()
    _reconcile_pending_action()
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
    }
    for name, column in definitions.items():
        if name not in columns:
            op.add_column("agent_message", column)

    op.execute(
        "ALTER TABLE agent_message MODIFY COLUMN user_message varchar(4000) NULL"
    )
    op.execute("ALTER TABLE agent_message MODIFY COLUMN biz_data mediumtext NULL")


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
            "quality_json": "json NULL",
            "reward_signals_json": "json NULL",
            "capture_level": "varchar(16) NOT NULL DEFAULT 'FULL'",
            "dataset_eligible": "varchar(16) NOT NULL DEFAULT 'UNREVIEWED'",
            "started_at": "datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)",
            "completed_at": "datetime(3) NULL",
            "created_at": "datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)",
            "updated_at": (
                "datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) "
                "ON UPDATE CURRENT_TIMESTAMP(3)"
            ),
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
        "UPDATE agent_step SET run_id=CONCAT('legacy-step-', step_id) "
        "WHERE run_id IS NULL OR run_id=''"
    )
    op.execute(
        "ALTER TABLE agent_step MODIFY COLUMN run_id varchar(64) NOT NULL"
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
        ),
        "agent_run": (
            ("uk_agent_run_message", ("message_id",), True),
            ("idx_agent_run_trace", ("otel_trace_id",), False),
            ("idx_agent_run_status_time", ("status", "started_at"), False),
            ("idx_agent_run_user_time", ("user_id", "started_at"), False),
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
        "agent_pending_action",
        "agent_shopping_profile",
        "agent_order_selection",
        "agent_session_memory",
        "agent_message",
    ):
        op.drop_table(table_name)
