from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

CURRENT_REVISION = "current"

_REQUIRED_COLUMNS = {
    "agent_message": {
        "message_id",
        "assistant_message",
        "user_message",
        "send_time",
        "user_id",
        "status",
        "biz_type",
        "biz_data",
        "session_id",
        "intent",
        "intent_confidence",
        "sentiment",
        "urgency",
        "risk_level",
        "trace_id",
        "source_refs",
        "latency_ms",
        "unresolved_count",
        "queue_name",
    },
    "agent_session_memory": {
        "user_id",
        "summary_json",
        "state_json",
        "turn_count",
        "updated_at",
    },
    "agent_pending_action": {
        "action_token",
        "user_id",
        "action_type",
        "message_id",
        "params_json",
        "status",
        "expires_at",
        "created_at",
        "updated_at",
    },
    "support_session": {"session_id", "user_id", "status", "created_at", "updated_at"},
    "support_message": {
        "support_message_id",
        "session_id",
        "sender_type",
        "content",
        "created_at",
    },
    "agent_task": {
        "task_id",
        "message_id",
        "user_id",
        "queue_name",
        "priority",
        "status",
        "payload_json",
        "created_at",
        "updated_at",
    },
    "agent_message_feedback": {
        "feedback_id",
        "message_id",
        "user_id",
        "rating",
        "created_at",
        "updated_at",
    },
    "ai_badcase_candidate": {
        "candidate_id",
        "candidate_type",
        "reason",
        "status",
        "created_at",
        "updated_at",
    },
}

_REQUIRED_INDEXES = {
    ("agent_message", "idx_agent_message_user"),
    ("agent_message", "idx_agent_message_session"),
    ("agent_pending_action", "idx_agent_pending_user"),
    ("support_session", "idx_support_queue"),
    ("support_message", "idx_support_message_session"),
    ("agent_task", "uk_agent_task_message"),
    ("agent_message_feedback", "uk_agent_feedback"),
    ("ai_badcase_candidate", "uk_badcase_message_type"),
}


def _normalize_existing_schema_history() -> None:
    """Collapse schema history only when the complete current contract exists."""
    from app.config.settings import get_settings

    settings = get_settings()
    engine = create_engine(
        settings.mysql_dsn.replace("mysql+aiomysql", "mysql+pymysql"),
        pool_pre_ping=True,
    )
    try:
        with engine.begin() as connection:
            has_history = connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_schema = DATABASE()
                      AND table_name = 'alembic_version'
                    """
                )
            ).scalar_one()
            column_rows = connection.execute(
                text(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = DATABASE()
                    """
                )
            ).all()
            available_columns: dict[str, set[str]] = {}
            for table_name, column_name in column_rows:
                available_columns.setdefault(str(table_name), set()).add(
                    str(column_name)
                )
            index_rows = connection.execute(
                text(
                    """
                    SELECT table_name, index_name
                    FROM information_schema.statistics
                    WHERE table_schema = DATABASE()
                    """
                )
            ).all()
            available_indexes = {
                (str(table_name), str(index_name))
                for table_name, index_name in index_rows
            }
            schema_is_current = all(
                required.issubset(available_columns.get(table_name, set()))
                for table_name, required in _REQUIRED_COLUMNS.items()
            ) and _REQUIRED_INDEXES.issubset(available_indexes)
            if not has_history:
                return
            connection.execute(text("DELETE FROM alembic_version"))
            if schema_is_current:
                connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                    {"revision": CURRENT_REVISION},
                )
    finally:
        engine.dispose()


def run_migrations() -> None:
    root = Path(__file__).resolve().parents[2]
    _normalize_existing_schema_history()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "scripts" / "alembic"))
    command.upgrade(config, "head")
