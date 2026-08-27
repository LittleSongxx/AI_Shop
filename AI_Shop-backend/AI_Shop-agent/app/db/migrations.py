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
        "image_asset_id",
        "image_snapshot_json",
        "selected_visual_subject_json",
        "latency_ms",
        "unresolved_count",
        "queue_name",
    },
    "agent_request_idempotency": {
        "user_id",
        "idempotency_key",
        "request_fingerprint",
        "run_id",
        "message_id",
        "status",
        "response_json",
        "created_at",
        "updated_at",
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
        "ttft_ms",
        "pilot_batch_id",
        "evidence_source",
        "quality_json",
        "reward_signals_json",
        "capture_level",
        "dataset_eligible",
        "dataset_reviewed_by",
        "dataset_reviewed_at",
        "dataset_review_note",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
        "agent_id",
        "agent_version",
        "parent_run_id",
        "handoff_id",
        "actor_type",
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
        "agent_id",
        "artifact_type",
        "handoff_id",
    },
    "agent_handoff": {
        "handoff_id", "parent_run_id", "child_run_id", "source_agent", "target_agent",
        "status", "input_summary_json", "artifact_summary_json", "latency_ms", "error_code",
        "completed_at", "created_at",
    },
    "agent_session_memory": {
        "user_id",
        "summary_json",
        "state_json",
        "turn_count",
        "history_cleared_through_message_id",
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
    "agent_visual_selection": {
        "selection_id",
        "user_id",
        "source_message_id",
        "image_asset_id",
        "original_text",
        "subjects_json",
        "constraints_json",
        "status",
        "expires_at",
        "selected_subject_id",
        "selected_message_id",
        "created_at",
        "updated_at",
    },
    "agent_pending_action": {
        "action_token",
        "user_id",
        "action_type",
        "message_id",
        "run_id",
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
        "client_event_id",
        "idempotency_key",
        "user_id",
        "request_id",
        "product_id",
        "position",
        "source",
        "retrieval_mode",
        "match_type",
        "subject_label",
        "recall_source",
        "model_version",
        "run_id",
        "event_type",
        "occurred_at",
        "created_at",
    },
    "support_session": {
        "session_id",
        "user_id",
        "status",
        "context_json",
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
    "support_case": {
        "case_id", "case_no", "user_id", "order_id", "order_item_id", "category",
        "status", "description", "evidence_json", "source_message_id", "run_id",
        "action_token", "idempotency_key", "priority", "forced_handoff", "assigned_admin",
        "support_session_id",
        "resolution_code", "root_cause", "resolution_summary", "created_at", "updated_at",
        "resolved_at",
    },
    "agent_shopping_mission": {
        "user_id", "mission_id", "status", "mission_json", "source_message_id",
        "revision", "expires_at", "created_at", "updated_at",
    },
    "agent_category_need_schema": {
        "schema_key", "version", "status", "schema_json", "created_by", "created_at",
        "updated_at",
    },
    "agent_product_decision_feature": {
        "feature_id", "product_id", "feature_key", "feature_value", "source_type",
        "evidence_json", "confidence", "review_status", "version", "valid_from",
        "valid_until", "reviewed_by", "reviewed_at", "created_at", "updated_at",
    },
    "agent_final_offer_snapshot": {
        "snapshot_id", "user_id", "product_id", "sku_key", "offer_json", "expires_at",
        "created_at",
    },
    "agent_ranking_policy_decision": {
        "decision_id", "request_id", "mission_id", "user_id", "policy_version",
        "decision_json", "created_at",
    },
    "agent_recommendation_explanation": {
        "explanation_id", "decision_id", "product_id", "position", "explanation_json",
        "created_at",
    },
    "commerce_outcome_ledger": {
        "ledger_id", "event_id", "source", "idempotency_key", "event_type", "user_id",
        "request_id", "run_id", "pilot_batch_id", "product_id", "sku_key", "order_id", "payload_json",
        "occurred_at", "created_at",
    },
    "agent_pilot_batch": {
        "batch_id", "name", "description", "evidence_source", "status",
        "consent_text_version", "created_by", "started_at", "closed_at",
        "created_at", "updated_at",
    },
    "agent_pilot_participant": {
        "participant_id", "batch_id", "pseudonym", "user_id_hash",
        "user_id_encrypted", "consented_at", "withdrawn_at", "status",
        "created_by", "created_at", "updated_at",
    },
    "user_privacy_job": {
        "job_id", "user_id", "job_type", "idempotency_key", "request_fingerprint",
        "status", "current_step", "steps_json", "retry_count", "error_code",
        "error_message", "export_path", "export_token_hash", "export_expires_at",
        "requested_at", "started_at", "completed_at", "updated_at",
    },
    "agent_after_sales_policy": {
        "policy_id", "version", "status", "priority", "scope_json", "rule_json",
        "effective_start", "effective_end", "created_by", "created_at", "updated_at",
    },
    "agent_after_sales_eligibility": {
        "decision_id", "user_id", "order_id", "order_item_id", "decision_json",
        "expires_at", "created_at",
    },
    "agent_inventory_supply_parameter": {
        "product_id", "sku_key", "supplier_ref", "lead_time_days", "safety_stock",
        "min_order_quantity", "review_period_days", "enabled", "updated_by", "updated_at",
    },
    "agent_inventory_inbound": {
        "inbound_id", "product_id", "sku_key", "quantity", "eta_date", "status",
        "source_ref", "updated_at",
    },
    "agent_inventory_forecast": {
        "forecast_id", "product_id", "sku_key", "forecast_json", "status", "generated_at",
        "reviewed_by", "reviewed_at",
    },
}

_REQUIRED_INDEXES = {
    ("agent_message", "idx_agent_message_user"),
    ("agent_message", "idx_agent_message_session"),
    ("agent_message", "uk_agent_message_run"),
    ("agent_message", "idx_agent_message_image_asset"),
    ("agent_request_idempotency", "uk_agent_request_idempotency_run"),
    ("agent_request_idempotency", "idx_agent_request_idempotency_message"),
    ("agent_run", "uk_agent_run_message"),
    ("agent_run", "idx_agent_run_trace"),
    ("agent_run", "idx_agent_run_status_time"),
    ("agent_run", "idx_agent_run_user_time"),
    ("agent_run", "idx_agent_run_agent_time"),
    ("agent_run", "idx_agent_run_parent_time"),
    ("agent_run", "idx_agent_run_pilot_time"),
    ("agent_handoff", "uk_agent_handoff_child"),
    ("agent_handoff", "idx_agent_handoff_parent"),
    ("agent_handoff", "idx_agent_handoff_status"),
    ("agent_step", "idx_agent_step_run_time"),
    ("agent_step", "idx_agent_step_type_status"),
    ("agent_order_selection", "idx_agent_selection_user_status"),
    ("agent_order_selection", "uk_agent_selection_message"),
    ("agent_visual_selection", "idx_visual_selection_user_status"),
    ("agent_visual_selection", "idx_visual_selection_source_message"),
    ("agent_visual_selection", "uk_visual_selection_message"),
    ("agent_pending_action", "idx_agent_pending_user"),
    ("agent_pending_action", "uk_agent_pending_active_business"),
    ("agent_pending_action", "idx_agent_pending_run"),
    ("agent_recommendation_event", "uk_agent_rec_event"),
    ("agent_recommendation_event", "uk_agent_rec_idempotency"),
    ("agent_recommendation_event", "uk_agent_rec_client_event"),
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
    ("support_case", "uk_support_case_no"),
    ("support_case", "uk_support_case_idempotency"),
    ("support_case", "idx_support_case_user_time"),
    ("support_case", "idx_support_case_status_time"),
    ("support_case", "idx_support_case_run"),
    ("agent_shopping_mission", "uk_agent_shopping_mission_id"),
    ("agent_shopping_mission", "idx_agent_shopping_mission_active"),
    ("agent_category_need_schema", "idx_category_need_schema_status"),
    ("agent_product_decision_feature", "uk_agent_product_feature_version"),
    ("agent_product_decision_feature", "idx_agent_product_feature_lookup"),
    ("agent_final_offer_snapshot", "idx_agent_offer_user_expiry"),
    ("agent_final_offer_snapshot", "idx_agent_offer_product_expiry"),
    ("agent_ranking_policy_decision", "uk_agent_ranking_request"),
    ("agent_ranking_policy_decision", "idx_agent_ranking_mission"),
    ("agent_ranking_policy_decision", "idx_agent_ranking_user_time"),
    ("agent_recommendation_explanation", "uk_agent_rec_explanation"),
    ("agent_recommendation_explanation", "idx_agent_rec_explanation_position"),
    ("commerce_outcome_ledger", "uk_commerce_outcome_source_key"),
    ("commerce_outcome_ledger", "uk_commerce_outcome_event"),
    ("commerce_outcome_ledger", "idx_commerce_outcome_request_time"),
    ("commerce_outcome_ledger", "idx_commerce_outcome_user_time"),
    ("commerce_outcome_ledger", "idx_commerce_outcome_product_time"),
    ("agent_pilot_batch", "idx_agent_pilot_batch_status"),
    ("agent_pilot_batch", "idx_agent_pilot_batch_source"),
    ("agent_pilot_participant", "uk_agent_pilot_participant_user"),
    ("agent_pilot_participant", "uk_agent_pilot_participant_alias"),
    ("agent_pilot_participant", "idx_agent_pilot_participant_status"),
    ("user_privacy_job", "uk_user_privacy_job_idempotency"),
    ("user_privacy_job", "idx_user_privacy_job_user_time"),
    ("user_privacy_job", "idx_user_privacy_job_status"),
    ("agent_after_sales_policy", "idx_after_sales_policy_effective"),
    ("agent_after_sales_eligibility", "idx_after_sales_eligibility_order"),
    ("agent_after_sales_eligibility", "idx_after_sales_eligibility_user_expiry"),
    ("agent_inventory_inbound", "idx_inventory_inbound_sku"),
    ("agent_inventory_forecast", "idx_inventory_forecast_status"),
    ("agent_inventory_forecast", "idx_inventory_forecast_sku"),
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


def _ensure_system_after_sales_policies() -> None:
    """Seed immutable global policy defaults for databases already at ``current``.

    The current Alembic revision is intentionally collapsed for legacy
    deployments, so editing the revision's upgrade body alone would not run on
    an existing database whose schema is already marked current.  ``INSERT
    IGNORE`` preserves operator-authored replacements while making the default
    RETURN rule available on both fresh and upgraded installations.
    """

    from app.config.settings import get_settings

    settings = get_settings()
    engine = create_engine(
        settings.mysql_dsn.replace("mysql+aiomysql", "mysql+pymysql"),
        pool_pre_ping=True,
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT IGNORE INTO agent_after_sales_policy
                        (policy_id, version, status, priority, scope_json, rule_json,
                         effective_start, effective_end, created_by, created_at, updated_at)
                    VALUES
                        ('system-return-state', 'v1', 'PUBLISHED', 0,
                         JSON_OBJECT('scopeType', 'GLOBAL'),
                         JSON_OBJECT(
                             'action', 'RETURN',
                             'orderStatuses', JSON_ARRAY(2, 3),
                             'itemStatuses', JSON_ARRAY(1),
                             'requiredEvidence', JSON_ARRAY()
                         ),
                         '2020-01-01 00:00:00.000', NULL,
                         'SYSTEM_MIGRATION', NOW(3), NOW(3))
                    """
                )
            )
    finally:
        engine.dispose()


def run_migrations() -> None:
    root = Path(__file__).resolve().parents[2]
    _normalize_existing_schema_history()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "scripts" / "alembic"))
    command.upgrade(config, "head")
    _ensure_system_after_sales_policies()
