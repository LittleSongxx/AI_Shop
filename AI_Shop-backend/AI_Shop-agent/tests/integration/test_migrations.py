from __future__ import annotations

import os
import uuid

import pymysql
import pytest

from app.config.settings import get_settings
from app.db.migrations import _REQUIRED_COLUMNS, CURRENT_REVISION, run_migrations

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
    databases = (fresh, original, incomplete)

    try:
        for database in databases:
            _create_database(database)

        _migrate(fresh)
        _migrate(fresh)
        _assert_current_schema(fresh)

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
    finally:
        get_settings.cache_clear()
        for database in databases:
            _drop_database(database)
