from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone

import pymysql
import pytest

from app.config.settings import get_settings
from app.db.migrations import _REQUIRED_COLUMNS, CURRENT_REVISION, run_migrations
from app.db.pool import close_pool, init_pool
from app.domain.intent.types import IntentDecision, IntentKind, NextAction
from app.services.badcase_service import badcase_service
from app.services.episode_service import EpisodeService, bind_episode
from app.services.order_selection_store import (
    OrderSelectionConflict,
    order_selection_store,
)
from app.services.recommendation_event_store import recommendation_event_store
from app.services.shopping_profile_service import (
    ProfileRevisionConflict,
    ShoppingProfileService,
)

pytestmark = pytest.mark.mysql


def _server_connection():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "root"),
        charset="utf8mb4",
        autocommit=True,
    )


def _database_connection(database: str):
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "root"),
        database=database,
        charset="utf8mb4",
        autocommit=True,
    )


def _create_database(database: str) -> None:
    with _server_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
        )


def _drop_database(database: str) -> None:
    with _server_connection() as connection, connection.cursor() as cursor:
        cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")


def _migrate(database: str) -> None:
    os.environ["MYSQL_DATABASE"] = database
    get_settings.cache_clear()
    run_migrations()


def _create_original_schema(database: str) -> None:
    with _database_connection(database) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE agent_message
            (
                message_id int AUTO_INCREMENT PRIMARY KEY,
                assistant_message text NULL,
                user_message varchar(500) NULL,
                send_time datetime NULL,
                user_id varchar(15) NULL,
                status tinyint DEFAULT 1 NULL,
                biz_type varchar(30) NULL,
                biz_data varchar(2000) NULL
            ) CHARSET = utf8mb4
            """
        )
        cursor.execute(
            """
            CREATE TABLE agent_session_memory
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
        cursor.execute(
            """
            CREATE TABLE alembic_version
            (
                version_num varchar(32) NOT NULL PRIMARY KEY
            )
            """
        )
        cursor.execute(
            "INSERT INTO alembic_version (version_num) VALUES ('removed_history')"
        )
        cursor.execute(
            """
            INSERT INTO agent_message
                (user_message, user_id, status, biz_data)
            VALUES ('preserve me', 'u1', 2, '{"ok":true}')
            """
        )


def _assert_current_schema(database: str) -> None:
    with _database_connection(database) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema=%s
            """,
            (database,),
        )
        available: dict[str, set[str]] = {}
        for table_name, column_name in cursor.fetchall():
            available.setdefault(table_name, set()).add(column_name)
        for table_name, columns in _REQUIRED_COLUMNS.items():
            assert columns.issubset(available.get(table_name, set()))

        cursor.execute("SELECT version_num FROM alembic_version")
        assert cursor.fetchone() == (CURRENT_REVISION,)

        cursor.execute(
            """
            SELECT character_maximum_length
            FROM information_schema.columns
            WHERE table_schema=%s AND table_name='agent_message'
              AND column_name='user_message'
            """,
            (database,),
        )
        assert cursor.fetchone() == (4000,)
        cursor.execute(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema=%s AND table_name='agent_message'
              AND column_name='biz_data'
            """,
            (database,),
        )
        assert cursor.fetchone() == ("mediumtext",)


@pytest.mark.skipif(
    os.getenv("RUN_AGENT_MIGRATION_TESTS") != "1",
    reason="requires a real MySQL 8 server",
)
def test_current_schema_migrates_fresh_original_and_incomplete_databases():
    suffix = uuid.uuid4().hex[:10]
    fresh = f"agent_migration_fresh_{suffix}"
    original = f"agent_migration_original_{suffix}"
    incomplete = f"agent_migration_incomplete_{suffix}"
    duplicates = f"agent_migration_duplicates_{suffix}"
    pending_legacy = f"agent_migration_pending_{suffix}"
    databases = (fresh, original, incomplete, duplicates, pending_legacy)

    try:
        for database in databases:
            _create_database(database)

        _migrate(fresh)
        _migrate(fresh)
        _assert_current_schema(fresh)
        with _database_connection(fresh) as connection, connection.cursor() as cursor:
            insert = """
                INSERT INTO agent_pending_action
                    (action_token, user_id, action_type, params_json, business_key,
                     args_fingerprint, status, expires_at, created_at, updated_at)
                VALUES (%s, 'u-dedupe', 'REFUND', '{"orderItemId":"item-1"}',
                        'u-dedupe:REFUND:item-1', %s, %s,
                        DATE_ADD(NOW(), INTERVAL 1 HOUR), NOW(), NOW())
            """
            cursor.execute(insert, ("act_first", "a" * 64, "PENDING"))
            with pytest.raises(pymysql.err.IntegrityError):
                cursor.execute(insert, ("act_second", "a" * 64, "PENDING"))
            cursor.execute(
                "UPDATE agent_pending_action SET status='MANUAL_REVIEW' "
                "WHERE action_token='act_first'"
            )
            with pytest.raises(pymysql.err.IntegrityError):
                cursor.execute(insert, ("act_second", "a" * 64, "PENDING"))
            cursor.execute(
                "UPDATE agent_pending_action SET status='CANCELLED' "
                "WHERE action_token='act_first'"
            )
            cursor.execute(insert, ("act_second", "a" * 64, "PENDING"))

        _create_original_schema(original)
        _migrate(original)
        _assert_current_schema(original)
        with _database_connection(original) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT user_message, biz_data FROM agent_message WHERE user_id='u1'"
            )
            assert cursor.fetchone() == ("preserve me", '{"ok":true}')

        _migrate(incomplete)
        with _database_connection(incomplete) as connection, connection.cursor() as cursor:
            cursor.execute("DROP TABLE support_message")
            cursor.execute("DROP TABLE agent_pending_action")
            cursor.execute("DROP TABLE agent_regression_case")
            cursor.execute("ALTER TABLE ai_badcase_candidate DROP COLUMN run_id")
            cursor.execute("ALTER TABLE agent_run DROP COLUMN capture_level")
            cursor.execute("ALTER TABLE agent_run DROP COLUMN scenario")
            cursor.execute("ALTER TABLE agent_step DROP COLUMN output_json")
            cursor.execute("ALTER TABLE agent_shopping_profile DROP COLUMN revision")
            cursor.execute(
                "ALTER TABLE agent_message MODIFY COLUMN user_message varchar(500) NULL"
            )
            cursor.execute(
                "ALTER TABLE agent_message MODIFY COLUMN biz_data varchar(2000) NULL"
            )
            cursor.execute(
                """
                INSERT INTO agent_message
                    (user_message, user_id, status, biz_data)
                VALUES ('still here', 'u2', 2, '{"kept":true}')
                """
            )
        _migrate(incomplete)
        _assert_current_schema(incomplete)
        with _database_connection(incomplete) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT user_message, biz_data FROM agent_message WHERE user_id='u2'"
            )
            assert cursor.fetchone() == ("still here", '{"kept":true}')

        # 模拟 alembic_version 已是 current、但待确认表仍是旧契约且已有业务数据。
        # 迁移不能把旧参数猜成新的业务键；必须使用 legacy:<token> 隔离回填。
        _migrate(pending_legacy)
        with _database_connection(pending_legacy) as connection, connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE agent_pending_action DROP INDEX uk_agent_pending_active_business"
            )
            cursor.execute("ALTER TABLE agent_pending_action DROP COLUMN active_business_key")
            for column in (
                "business_key",
                "args_fingerprint",
                "reconcile_attempts",
                "reconcile_deadline",
                "last_reconcile_at",
                "review_reason",
            ):
                cursor.execute(f"ALTER TABLE agent_pending_action DROP COLUMN {column}")
            cursor.execute(
                """
                INSERT INTO agent_pending_action
                    (action_token, user_id, action_type, message_id, params_json,
                     summary, status, expires_at, created_at, updated_at)
                VALUES
                    ('act_legacy_row', 'u-old', 'REFUND', 1,
                     '{"orderItemId":"legacy_item"}', '历史退款提案', 'PENDING',
                     DATE_ADD(NOW(), INTERVAL 1 HOUR), NOW(), NOW())
                """
            )
        _migrate(pending_legacy)
        _assert_current_schema(pending_legacy)
        with _database_connection(pending_legacy) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT business_key, LENGTH(args_fingerprint), active_business_key,
                       reconcile_attempts
                FROM agent_pending_action WHERE action_token='act_legacy_row'
                """
            )
            assert cursor.fetchone() == (
                "legacy:act_legacy_row",
                64,
                "legacy:act_legacy_row",
                0,
            )

        # 模拟旧版本允许同一用户存在多个活跃人工会话的数据库。升级必须保留
        # 两条会话记录，并把重复会话的消息迁入最早创建的主会话；直接 DELETE
        # 会话会让 support_message 留下孤儿记录。
        _migrate(duplicates)
        with _database_connection(duplicates) as connection, connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE support_session DROP INDEX uk_support_active_user"
            )
            cursor.execute(
                """
                INSERT INTO support_session
                    (session_id, user_id, status, summary, assigned_admin,
                     created_at, updated_at)
                VALUES
                    ('session-old', 'merge-user', 'QUEUED', '旧排队会话', NULL,
                     '2026-01-01 00:00:00', '2026-01-01 00:00:00'),
                    ('session-new', 'merge-user', 'ACTIVE', '正在服务的主会话', 'admin-1',
                     '2026-01-02 00:00:00', '2026-01-02 00:00:00')
                """
            )
            cursor.execute(
                """
                INSERT INTO support_message (session_id, sender_type, content)
                VALUES ('session-old', 'USER', 'old-message'),
                       ('session-new', 'USER', 'new-message')
                """
            )
            cursor.execute(
                """
                INSERT INTO agent_message
                    (session_id, user_id, user_message, status, send_time)
                VALUES ('session-old', 'merge-user', 'linked-agent-message', 2, NOW())
                """
            )

        _migrate(duplicates)
        _migrate(duplicates)
        _assert_current_schema(duplicates)
        with _database_connection(duplicates) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT session_id, status, summary
                FROM support_session
                WHERE user_id='merge-user'
                ORDER BY created_at, session_id
                """
            )
            sessions = cursor.fetchall()
            assert len(sessions) == 2
            assert sessions[0][0:2] == ("session-old", "CANCELLED")
            assert "session-new" in sessions[0][2]
            assert sessions[1] == ("session-new", "ACTIVE", "正在服务的主会话")

            cursor.execute(
                """
                SELECT session_id, content
                FROM support_message
                WHERE content IN ('old-message', 'new-message')
                ORDER BY support_message_id
                """
            )
            assert cursor.fetchall() == (
                ("session-new", "old-message"),
                ("session-new", "new-message"),
            )
            cursor.execute(
                """
                SELECT session_id FROM agent_message
                WHERE user_message='linked-agent-message'
                """
            )
            assert cursor.fetchone() == ("session-new",)
    finally:
        get_settings.cache_clear()
        for database in databases:
            _drop_database(database)


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_AGENT_MIGRATION_TESTS") != "1",
    reason="requires a real MySQL 8 server",
)
async def test_recommendation_event_facts_are_deduplicated_and_validated():
    database = f"agent_migration_rec_{uuid.uuid4().hex[:10]}"
    request_id = uuid.uuid4().hex
    try:
        _create_database(database)
        _migrate(database)
        await init_pool()

        await recommendation_event_store.record_impressions(
            "u1", request_id, ["p1", "p2"], "hybrid"
        )
        await recommendation_event_store.record_impressions(
            "u1", request_id, ["p1", "p2"], "hybrid"
        )
        first = await recommendation_event_store.record_click(
            "u1", request_id, "p2", 2
        )
        duplicate = await recommendation_event_store.record_click(
            "u1", request_id, "p2", 2
        )

        assert first is not None
        assert duplicate == first
        assert await recommendation_event_store.record_click(
            "u2", request_id, "p2", 2
        ) is None
        assert await recommendation_event_store.record_click(
            "u1", request_id, "p1", 2
        ) is None
        assert await recommendation_event_store.record_click(
            "u1", request_id, "p2", 1
        ) is None

        validated = await recommendation_event_store.validate_batch(
            "u1",
            [
                {"requestId": request_id, "productId": "p2", "position": 2},
                {"requestId": request_id, "productId": "p1", "position": 1},
            ],
        )
        assert validated == [first]

        with _database_connection(database) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT event_type, COUNT(*) FROM agent_recommendation_event "
                "GROUP BY event_type ORDER BY event_type"
            )
            assert cursor.fetchall() == (("CLICK", 1), ("IMPRESSION", 2))
    finally:
        await close_pool()
        get_settings.cache_clear()
        _drop_database(database)


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_AGENT_MIGRATION_TESTS") != "1",
    reason="requires a real MySQL 8 server",
)
async def test_shopping_profile_manual_updates_use_revision_cas():
    database = f"agent_migration_profile_{uuid.uuid4().hex[:10]}"
    service = ShoppingProfileService()
    try:
        _create_database(database)
        _migrate(database)
        await init_pool()

        first = await service.manual_update(
            "u-profile",
            {"category": "手机", "brands": ["华为"]},
            expected_revision=0,
        )
        assert first["revision"] == 1
        assert first["fieldMeta"]["category"]["source"] == "MANUAL"
        manual_expiry = datetime.fromisoformat(
            first["fieldMeta"]["category"]["expiresAt"].replace("Z", "+00:00")
        )
        remaining_days = (manual_expiry - datetime.now(timezone.utc)).days
        assert 179 <= remaining_days <= 180

        with pytest.raises(ProfileRevisionConflict) as conflict:
            await service.manual_update(
                "u-profile", {"brands": ["苹果"]}, expected_revision=0
            )
        assert conflict.value.current["revision"] == 1
        assert conflict.value.current["brands"] == ["华为"]

        second = await service.manual_update(
            "u-profile", {"budgetMax": 5000}, expected_revision=1
        )
        assert second["revision"] == 2
        assert second["budgetMax"] == 5000

        cleared = await service.clear_profile("u-profile", expected_revision=2)
        assert cleared["revision"] == 3
        assert cleared["category"] is None
        assert cleared["brands"] == []
    finally:
        await close_pool()
        get_settings.cache_clear()
        _drop_database(database)


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_AGENT_MIGRATION_TESTS") != "1",
    reason="requires a real MySQL 8 server",
)
async def test_episode_writer_persists_sanitized_ordered_trace():
    database = f"agent_migration_episode_{uuid.uuid4().hex[:10]}"
    service = EpisodeService()
    try:
        _create_database(database)
        _migrate(database)
        await init_pool()
        await service.start()
        keep = service.start_run(
            run_id="run-episode-1",
            message_id=101,
            user_id="u1",
            session_id=None,
            intent="REFUND",
            queue_name="agent.high",
            force_keep=True,
        )
        with bind_episode(
            "run-episode-1", message_id=101, user_id="u1", force_keep=keep
        ):
            service.mark_running()
            service.record_step(
                "TOOL_CALL",
                tool_name="QUERY_ORDERS",
                input_data={
                    "userMessage": "手机号13812345678",
                    "orderId": "ORDER2026080712345678",
                },
            )
            service.update_run(quality={"verifierPassed": True})
            service.update_run(
                quality={"judge": {"groundedness": 0.9, "lowScore": False}}
            )
            service.finish_run("ok")
        await asyncio.sleep(0.4)
        await service.close()

        with _database_connection(database) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT status,capture_level FROM agent_run WHERE run_id=%s",
                ("run-episode-1",),
            )
            assert cursor.fetchone() == ("SUCCEEDED", "FULL")
            cursor.execute(
                "SELECT input_json FROM agent_step WHERE run_id=%s ORDER BY step_id",
                ("run-episode-1",),
            )
            payload = cursor.fetchone()[0]
            assert "13812345678" not in payload
            assert "ORDER2026080712345678" not in payload
            cursor.execute(
                "SELECT quality_json FROM agent_run WHERE run_id=%s",
                ("run-episode-1",),
            )
            quality = json.loads(cursor.fetchone()[0])
            assert quality["verifierPassed"] is True
            assert quality["judge"]["groundedness"] == 0.9
    finally:
        await service.close()
        await close_pool()
        get_settings.cache_clear()
        _drop_database(database)


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_AGENT_MIGRATION_TESTS") != "1",
    reason="requires a real MySQL 8 server",
)
async def test_badcase_lifecycle_requires_reviewed_passing_regression_case():
    database = f"agent_migration_badcase_{uuid.uuid4().hex[:10]}"
    try:
        _create_database(database)
        _migrate(database)
        with _database_connection(database) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO agent_message
                    (user_message,assistant_message,send_time,user_id,status,intent)
                VALUES ('订单为什么没退款','无法确认',NOW(),'u1',2,'REFUND_STATUS')
                """
            )
            message_id = cursor.lastrowid
        await init_pool()

        candidate_id = await badcase_service.add_candidate(
            message_id,
            "VERIFIER_FAILURE",
            "动态事实无工具依据",
            source="VERIFIER",
            severity="HIGH",
        )
        with pytest.raises(ValueError, match="不能从 NEW"):
            await badcase_service.review(
                candidate_id, "FIXING", "admin", owner="owner"
            )
        await badcase_service.review(candidate_id, "TRIAGED", "admin")
        await badcase_service.review(
            candidate_id, "LABELED", "admin", labels=["grounding", "refund"]
        )
        await badcase_service.review(
            candidate_id, "FIXING", "admin", owner="agent-team"
        )
        regression_added = await badcase_service.review(
            candidate_id,
            "REGRESSION_ADDED",
            "admin",
            regression={
                "name": "退款状态必须有事实依据",
                "scenario": "REFUND_STATUS",
                "input": {"userMessage": "订单为什么没退款"},
                "expected": {"requiredTools": ["QUERY_REFUND_STATUS"]},
            },
        )
        case_id = int(regression_added["regressionCaseId"])
        with pytest.raises(ValueError, match="PASS"):
            await badcase_service.review(candidate_id, "VERIFIED", "admin")
        await badcase_service.record_regression_result(case_id, "PASS")
        await badcase_service.review(candidate_id, "VERIFIED", "admin")
        closed = await badcase_service.review(candidate_id, "CLOSED", "admin")

        assert closed["status"] == "CLOSED"
        assert closed["labels"] == ["grounding", "refund"]
        cases = await badcase_service.list_regression_cases()
        assert cases["totalCount"] == 1
        assert cases["list"][0]["lastResult"] == "PASS"
    finally:
        await close_pool()
        get_settings.cache_clear()
        _drop_database(database)


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_AGENT_MIGRATION_TESTS") != "1",
    reason="requires a real MySQL 8 server",
)
async def test_order_selection_consumption_is_atomic_idempotent_and_recoverable():
    database = f"agent_migration_selection_{uuid.uuid4().hex[:10]}"
    candidates = [
        {
            "targetType": "ORDER_ITEM",
            "targetId": "SMITEM202608050002",
            "orderId": "SM202608050002",
            "orderItemId": "SMITEM202608050002",
            "productName": "索尼无线降噪耳机",
        },
        {
            "targetType": "ORDER_ITEM",
            "targetId": "SMITEM202608040001",
            "orderId": "SM202608040001",
            "orderItemId": "SMITEM202608040001",
            "productName": "苹果无线耳机",
        },
    ]
    decision = IntentDecision(
        intent=IntentKind.REFUND,
        confidence=1.0,
        next_action=NextAction.TOOL,
        source="order_selection",
    )
    try:
        _create_database(database)
        _migrate(database)
        await init_pool()

        selection = await order_selection_store.create(
            user_id="u1",
            source_message_id=30,
            intent="REFUND",
            original_text="没发货的耳机我要退款",
            candidates=candidates,
            context={"intentDecision": decision.model_dump(mode="json")},
        )
        kwargs = {
            "selection_id": selection["selectionId"],
            "user_id": "u1",
            "target_type": "ORDER_ITEM",
            "target_id": "SMITEM202608050002",
            "message": "选择索尼无线降噪耳机订单继续退款。",
            "decision": decision,
            "previous_unresolved_count": 0,
            "queue_name": "agent.high",
            "priority": 100,
            "trace_id": uuid.uuid4().hex,
            "selected_reference": {
                **candidates[0],
                "intent": "REFUND",
                "expiresAt": selection["expiresAt"],
            },
        }
        first, created = await order_selection_store.consume_with_message_and_task(
            **kwargs
        )
        repeated, repeated_created = (
            await order_selection_store.consume_with_message_and_task(**kwargs)
        )

        assert created is True
        assert repeated_created is False
        assert repeated["messageId"] == first["messageId"]
        with pytest.raises(OrderSelectionConflict):
            await order_selection_store.consume_with_message_and_task(
                **{
                    **kwargs,
                    "target_id": "SMITEM202608040001",
                    "selected_reference": candidates[1],
                }
            )

        rollback_selection = await order_selection_store.create(
            user_id="u1",
            source_message_id=31,
            intent="REFUND",
            original_text="退耳机",
            candidates=[candidates[0]],
        )
        rollback_trace = uuid.uuid4().hex
        with pytest.raises(pymysql.err.DataError):
            await order_selection_store.consume_with_message_and_task(
                **{
                    **kwargs,
                    "selection_id": rollback_selection["selectionId"],
                    "priority": 1000,
                    "trace_id": rollback_trace,
                }
            )

        stale_selection = await order_selection_store.create(
            user_id="u1",
            source_message_id=32,
            intent="REFUND",
            original_text="退耳机",
            candidates=[candidates[0]],
        )
        with _database_connection(database) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE agent_order_selection
                SET status='PROCESSING', selected_target_type='ORDER_ITEM',
                    selected_target_id='SMITEM202608050002',
                    updated_at=DATE_SUB(NOW(), INTERVAL 5 MINUTE)
                WHERE selection_id=%s
                """,
                (stale_selection["selectionId"],),
            )
        stale_message, stale_created = (
            await order_selection_store.consume_with_message_and_task(
                **{
                    **kwargs,
                    "selection_id": stale_selection["selectionId"],
                    "trace_id": uuid.uuid4().hex,
                }
            )
        )
        assert stale_created is True
        assert stale_message["messageId"] != first["messageId"]

        with _database_connection(database) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, selected_message_id
                FROM agent_order_selection WHERE selection_id=%s
                """,
                (selection["selectionId"],),
            )
            assert cursor.fetchone() == ("CONSUMED", first["messageId"])
            cursor.execute(
                "SELECT COUNT(*) FROM agent_message WHERE trace_id=%s",
                (kwargs["trace_id"],),
            )
            assert cursor.fetchone() == (1,)
            cursor.execute(
                "SELECT COUNT(*) FROM agent_task WHERE message_id=%s",
                (first["messageId"],),
            )
            assert cursor.fetchone() == (1,)
            cursor.execute(
                "SELECT status FROM agent_order_selection WHERE selection_id=%s",
                (rollback_selection["selectionId"],),
            )
            assert cursor.fetchone() == ("ACTIVE",)
            cursor.execute(
                "SELECT COUNT(*) FROM agent_message WHERE trace_id=%s",
                (rollback_trace,),
            )
            assert cursor.fetchone() == (0,)
    finally:
        await close_pool()
        get_settings.cache_clear()
        _drop_database(database)
