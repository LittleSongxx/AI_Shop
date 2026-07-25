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
            trace_id           varchar(64) NULL,
            source_refs        json NULL,
            latency_ms         int NULL,
            unresolved_count   int DEFAULT 0 NOT NULL,
            queue_name         varchar(64) NULL,
            KEY idx_agent_message_user (user_id, message_id),
            KEY idx_agent_message_session (session_id, message_id),
            KEY idx_agent_message_quality (intent, sentiment, send_time)
        ) COLLATE = utf8mb4_general_ci ROW_FORMAT = DYNAMIC
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
        CREATE TABLE IF NOT EXISTS agent_pending_action
        (
            action_token varchar(80) NOT NULL PRIMARY KEY,
            user_id varchar(32) NOT NULL,
            action_type varchar(64) NOT NULL,
            message_id bigint NULL,
            params_json json NOT NULL,
            summary varchar(512) NULL,
            confirm_text varchar(64) NULL,
            risk_tip varchar(512) NULL,
            status varchar(16) NOT NULL,
            result_message text NULL,
            error_message text NULL,
            expires_at datetime NOT NULL,
            created_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL
                ON UPDATE CURRENT_TIMESTAMP,
            KEY idx_agent_pending_user (user_id, status, expires_at),
            KEY idx_agent_pending_expire (status, expires_at)
        ) CHARSET = utf8mb4
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
            KEY idx_support_queue (status, urgency, created_at),
            KEY idx_support_user (user_id, status, updated_at)
        ) COMMENT 'human support session' CHARSET = utf8mb4
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
            candidate_type varchar(32) NOT NULL,
            reason varchar(255) NOT NULL,
            status varchar(16) DEFAULT 'PENDING' NOT NULL,
            snapshot_json json NULL,
            reviewer varchar(100) NULL,
            review_remark varchar(500) NULL,
            created_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at datetime DEFAULT CURRENT_TIMESTAMP NOT NULL
                ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT uk_badcase_message_type UNIQUE (message_id, candidate_type),
            KEY idx_badcase_status (status, created_at)
        ) COMMENT 'Agent badcase review pool' CHARSET = utf8mb4
        """
    )

    _reconcile_agent_message()
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


def _reconcile_indexes() -> None:
    indexes = {
        "agent_message": (
            ("idx_agent_message_user", ("user_id", "message_id"), False),
            ("idx_agent_message_session", ("session_id", "message_id"), False),
            (
                "idx_agent_message_quality",
                ("intent", "sentiment", "send_time"),
                False,
            ),
        ),
        "agent_pending_action": (
            (
                "idx_agent_pending_user",
                ("user_id", "status", "expires_at"),
                False,
            ),
            ("idx_agent_pending_expire", ("status", "expires_at"), False),
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
        "ai_badcase_candidate",
        "agent_message_feedback",
        "agent_task",
        "support_message",
        "support_session",
        "agent_pending_action",
        "agent_session_memory",
        "agent_message",
    ):
        op.drop_table(table_name)
