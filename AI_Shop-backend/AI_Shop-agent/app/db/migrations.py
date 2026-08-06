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
        "run_id",
        "trace_id",
        "source_refs",
        "latency_ms",
        "unresolved_count",
        "queue_name",
    },
    "agent_run": {
        "run_id",
        "message_id",
        "user_id",
        "session_id",
        "otel_trace_id",
        "status",
        "outcome",
        "scenario",
        "intent",
        "queue_name",
        "model_name",
        "version_json",
        "experiment_json",
        "input_tokens",
        "output_tokens",
        "cost_cny",
        "latency_ms",
        "quality_json",
        "reward_signals_json",
        "capture_level",
        "dataset_eligible",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    },
    "agent_step": {
        "step_id",
        "run_id",
        "event_type",
        "node_name",
        "round_no",
        "status",
        "span_id",
        "input_json",
        "output_json",
        "model_name",
        "tool_name",
        "call_id",
        "error_code",
        "error_message",
        "latency_ms",
        "occurred_at",
    },
    "agent_session_memory": {
        "user_id",
        "summary_json",
        "state_json",
        "turn_count",
        "updated_at",
    },
    "agent_order_selection": {
        "selection_id",
        "user_id",
        "source_message_id",
        "intent",
        "original_text",
        "candidates_json",
        "context_json",
        "status",
        "expires_at",
        "selected_target_type",
        "selected_target_id",
        "selected_message_id",
        "created_at",
        "updated_at",
    },
    "agent_shopping_profile": {
        "user_id",
        "profile_json",
        "revision",
        "updated_at",
    },
    "agent_pending_action": {
        "action_token",
        "user_id",
        "action_type",
        "message_id",
        "params_json",
        "business_key",
        "args_fingerprint",
        "active_business_key",
        "status",
        "reconcile_attempts",
        "reconcile_deadline",
        "last_reconcile_at",
        "review_reason",
        "expires_at",
        "created_at",
        "updated_at",
    },
    "agent_recommendation_event": {
        "event_id",
        "user_id",
        "request_id",
        "product_id",
        "position",
        "source",
        "event_type",
        "occurred_at",
        "created_at",
    },
    "support_session": {
        "session_id",
        "user_id",
        "status",
        "created_at",
        "updated_at",
        # P0-6：active_user 生成列（QUEUED/ASSIGNED/ACTIVE 时 = user_id）。
        # 清单与 alembic 迁移定义对齐，否则只缺这一列的环境会被判为
        # schema_is_current、跳过 alembic 补列（P1 审查）。
        "active_user",
    },
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
        "lease_owner",
        "lease_until",
        "next_retry_at",
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
        "message_id",
        "run_id",
        "candidate_type",
        "reason",
        "status",
        "source",
        "severity",
        "snapshot_json",
        "labels_json",
        "judge_json",
        "owner",
        "fix_version",
        "regression_case_id",
        "occurrence_count",
        "first_seen_at",
        "reviewer",
        "review_remark",
        "created_at",
        "updated_at",
    },
    "agent_regression_case": {
        "case_id",
        "candidate_id",
        "case_key",
        "name",
        "scenario",
        "input_json",
        "expected_json",
        "status",
        "created_by",
        "last_result",
        "last_run_at",
        "created_at",
        "updated_at",
    },
}

_REQUIRED_INDEXES = {
    ("agent_message", "idx_agent_message_user"),
    ("agent_message", "idx_agent_message_session"),
    ("agent_message", "uk_agent_message_run"),
    ("agent_run", "uk_agent_run_message"),
    ("agent_run", "idx_agent_run_trace"),
    ("agent_run", "idx_agent_run_status_time"),
    ("agent_run", "idx_agent_run_user_time"),
    ("agent_step", "idx_agent_step_run_time"),
    ("agent_step", "idx_agent_step_type_status"),
    ("agent_order_selection", "idx_agent_selection_user_status"),
    ("agent_order_selection", "uk_agent_selection_message"),
    ("agent_pending_action", "idx_agent_pending_user"),
    ("agent_pending_action", "uk_agent_pending_active_business"),
    ("agent_recommendation_event", "uk_agent_rec_event"),
    ("agent_recommendation_event", "idx_agent_rec_user_time"),
    ("agent_recommendation_event", "idx_agent_rec_request_type"),
    ("support_session", "idx_support_queue"),
    ("support_session", "uk_support_active_user"),
    ("support_message", "idx_support_message_session"),
    ("agent_task", "uk_agent_task_message"),
    ("agent_message_feedback", "uk_agent_feedback"),
    ("ai_badcase_candidate", "uk_badcase_message_type"),
    ("ai_badcase_candidate", "idx_badcase_run"),
    ("agent_regression_case", "uk_agent_regression_case_key"),
    ("agent_regression_case", "idx_agent_regression_status"),
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
