from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pymysql
from pymysql.constants import FIELD_TYPE

from evaluation.text2sql.catalog import CATALOG_VERSION, build_catalog
from evaluation.text2sql.contracts import ResultOracle, Text2SqlCase
from evaluation.text2sql.dataset import DEFAULT_DATASET, load_cases, validate_v0, write_cases
from evaluation.text2sql.io import canonical_json_bytes, sha256_bytes, sha256_file

PACKAGE_DIR = Path(__file__).resolve().parent
AGENT_ROOT = PACKAGE_DIR.parents[1]
BACKEND_ROOT = PACKAGE_DIR.parents[2]
FIXTURE_DIR = PACKAGE_DIR / "fixture"
COMPOSE_FILE = FIXTURE_DIR / "compose.yaml"
SQL_DIR = FIXTURE_DIR / "sql"

MYSQL_PORT = int(os.getenv("TEXT2SQL_MYSQL_PORT", "13316"))
REDIS_PORT = int(os.getenv("TEXT2SQL_REDIS_PORT", "16380"))
ROOT_PASSWORD = os.getenv("TEXT2SQL_MYSQL_ROOT_PASSWORD", "text2sql-root-local-only")
MIGRATOR_USER = "text2sql_migrator"
MIGRATOR_PASSWORD = "text2sql-migrator-local-only"
RUNTIME_USER = "text2sql_agent"
RUNTIME_PASSWORD = "text2sql-agent-local-only"
ADMIN_USER = "text2sql_admin"
ADMIN_PASSWORD = "text2sql-admin-local-only"
READER_USER = "text2sql_reader"
READER_PASSWORD = "text2sql-reader-local-only"
FIXED_TIMESTAMP = "2026-08-27 12:00:00"

DATABASES = (
    "aishop_admin",
    "aishop_agent",
    "aishop_cart",
    "aishop_coupon",
    "aishop_order",
    "aishop_pay",
    "aishop_product",
    "aishop_search",
    "aishop_stock",
    "aishop_user",
)

SOURCE_DATA_DATABASES = tuple(
    database for database in DATABASES if database not in {"aishop_admin", "aishop_agent"}
)

SERVICE_MIGRATIONS = (
    ("aishop_user", BACKEND_ROOT / "AI_Shop-user/app/src/main/resources/db/migration/R__current_schema.sql"),
    ("aishop_product", BACKEND_ROOT / "AI_Shop-product/app/src/main/resources/db/migration/R__current_schema.sql"),
    ("aishop_cart", BACKEND_ROOT / "AI_Shop-cart/app/src/main/resources/db/migration/R__current_schema.sql"),
    ("aishop_order", BACKEND_ROOT / "AI_Shop-order/app/src/main/resources/db/migration/R__current_schema.sql"),
    ("aishop_coupon", BACKEND_ROOT / "AI_Shop-coupon/app/src/main/resources/db/migration/R__current_schema.sql"),
    ("aishop_stock", BACKEND_ROOT / "AI_Shop-stock/app/src/main/resources/db/migration/R__current_schema.sql"),
    ("aishop_pay", BACKEND_ROOT / "AI_Shop-pay/app/src/main/resources/db/migration/R__current_schema.sql"),
    ("aishop_search", BACKEND_ROOT / "AI_Shop-search/src/main/resources/db/migration/R__current_schema.sql"),
)
ADMIN_MIGRATION = (
    BACKEND_ROOT / "AI_Shop-admin/src/main/resources/db/migration/R__current_schema.sql"
)


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
        cwd=AGENT_ROOT,
        check=check,
        text=True,
        capture_output=True,
    )


def _mysql_connection(*, user: str, password: str, database: str | None = None):
    return pymysql.connect(
        host="127.0.0.1",
        port=MYSQL_PORT,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _root_statements(statements: list[str]) -> None:
    with _mysql_connection(user="root", password=ROOT_PASSWORD) as connection:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)


def _mysql_file(database: str, path: Path, *, user: str = MIGRATOR_USER) -> None:
    password = MIGRATOR_PASSWORD if user == MIGRATOR_USER else ROOT_PASSWORD
    command = [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "exec",
        "-T",
        "-e",
        f"MYSQL_PWD={password}",
        "mysql",
        "mysql",
        f"-u{user}",
        "--binary-mode=1",
        "--default-character-set=utf8mb4",
        database,
    ]
    result = subprocess.run(
        command,
        cwd=AGENT_ROOT,
        input=path.read_text(encoding="utf-8"),
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(f"migration failed for {path}: {result.stderr.strip()}")


def up() -> dict[str, Any]:
    result = _compose("up", "-d", "--wait")
    return {"started": True, "stdout": result.stdout.strip(), "mysqlPort": MYSQL_PORT, "redisPort": REDIS_PORT}


def down() -> dict[str, Any]:
    result = _compose("down", "--volumes", "--remove-orphans", check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return {"stopped": True, "stdout": result.stdout.strip()}


def _bootstrap_identities() -> None:
    statements = [
        *(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci" for database in DATABASES),
        f"CREATE USER IF NOT EXISTS '{MIGRATOR_USER}'@'%' IDENTIFIED BY '{MIGRATOR_PASSWORD}'",
        f"ALTER USER '{MIGRATOR_USER}'@'%' IDENTIFIED BY '{MIGRATOR_PASSWORD}'",
        f"CREATE USER IF NOT EXISTS '{RUNTIME_USER}'@'%' IDENTIFIED BY '{RUNTIME_PASSWORD}'",
        f"ALTER USER '{RUNTIME_USER}'@'%' IDENTIFIED BY '{RUNTIME_PASSWORD}'",
        f"CREATE USER IF NOT EXISTS '{ADMIN_USER}'@'%' IDENTIFIED BY '{ADMIN_PASSWORD}'",
        f"ALTER USER '{ADMIN_USER}'@'%' IDENTIFIED BY '{ADMIN_PASSWORD}'",
        f"CREATE USER IF NOT EXISTS '{READER_USER}'@'%' IDENTIFIED BY '{READER_PASSWORD}'",
        f"ALTER USER '{READER_USER}'@'%' IDENTIFIED BY '{READER_PASSWORD}'",
        *(f"GRANT ALL PRIVILEGES ON `{database}`.* TO '{MIGRATOR_USER}'@'%'" for database in DATABASES),
        f"GRANT ALL PRIVILEGES ON `aishop_agent`.* TO '{RUNTIME_USER}'@'%'",
        f"GRANT ALL PRIVILEGES ON `aishop_admin`.* TO '{ADMIN_USER}'@'%'",
        "FLUSH PRIVILEGES",
    ]
    _root_statements(list(statements))


def _run_agent_migration() -> None:
    environment = dict(os.environ)
    environment.update(
        {
            "APP_ENV": "development",
            "MYSQL_HOST": "127.0.0.1",
            "MYSQL_PORT": str(MYSQL_PORT),
            "MYSQL_USER": MIGRATOR_USER,
            "MYSQL_PASSWORD": MIGRATOR_PASSWORD,
            "MYSQL_DATABASE": "aishop_agent",
            "DATA_ANALYST_ENABLED": "false",
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": str(REDIS_PORT),
            "AISHOP_INTERNAL_TOKEN": "text2sql-eval-internal-token",
            "ADMIN_ASSERTION_CURRENT_SECRET": "text2sql-eval-admin-assertion-secret",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=AGENT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(f"Agent Alembic migration failed: {result.stderr.strip()}")


def _grant_reader() -> None:
    views = sorted(build_catalog()["views"])
    statements = [f"REVOKE ALL PRIVILEGES, GRANT OPTION FROM '{READER_USER}'@'%'"]
    statements.extend(
        f"GRANT SELECT, SHOW VIEW ON `aishop_admin`.`{view}` TO '{READER_USER}'@'%'"
        for view in views
    )
    statements.append("FLUSH PRIVILEGES")
    _root_statements(statements)


def bootstrap(*, state: str = "base") -> dict[str, Any]:
    if state not in {"base", "boundary", "empty"}:
        raise ValueError(f"unknown fixture state: {state}")
    up()
    _bootstrap_identities()
    for database, migration in SERVICE_MIGRATIONS:
        _mysql_file(database, migration)
    _run_agent_migration()
    _mysql_file("aishop_admin", ADMIN_MIGRATION)
    _grant_reader()
    reset(state)
    report = verify()
    report["state"] = state
    return report


def reset(state: str) -> dict[str, Any]:
    if state not in {"base", "boundary", "empty"}:
        raise ValueError(f"unknown fixture state: {state}")
    _mysql_file("aishop_admin", SQL_DIR / "reset.sql")
    if state in {"base", "boundary"}:
        _mysql_file("aishop_admin", SQL_DIR / "base.sql")
    if state == "boundary":
        _mysql_file("aishop_admin", SQL_DIR / "boundary.sql")
    redis_result = _compose("exec", "-T", "redis", "redis-cli", "FLUSHALL")
    return {"state": state, "redis": redis_result.stdout.strip()}


def verify() -> dict[str, Any]:
    catalog = build_catalog()
    views = sorted(catalog["views"])
    view_checks: dict[str, bool] = {}
    denied_checks: dict[str, bool] = {}
    with _mysql_connection(
        user=READER_USER, password=READER_PASSWORD, database="aishop_admin"
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION time_zone = '+08:00'")
            cursor.execute(f"SET SESSION timestamp = UNIX_TIMESTAMP('{FIXED_TIMESTAMP}')")
            cursor.execute("SELECT VERSION() AS version, CURRENT_DATE AS fixed_date")
            server = cursor.fetchone()
            for view in views:
                cursor.execute(f"SELECT 1 FROM `{view}` LIMIT 0")
                cursor.execute(f"SHOW CREATE VIEW `{view}`")
                view_checks[view] = cursor.fetchone() is not None
            forbidden = {
                "sourceOrderRead": "SELECT * FROM aishop_order.order_info LIMIT 1",
                "sourceProductRead": "SELECT * FROM aishop_product.product_info LIMIT 1",
                "crossSchemaSystemRead": "SELECT * FROM mysql.user LIMIT 1",
                "viewWrite": "UPDATE analytics_inventory_risk SET stock = 999 LIMIT 1",
                "createTable": "CREATE TABLE text2sql_forbidden(id INT)",
            }
            for name, statement in forbidden.items():
                try:
                    cursor.execute(statement)
                except pymysql.MySQLError:
                    denied_checks[name] = True
                else:
                    denied_checks[name] = False
    with _mysql_connection(user="root", password=ROOT_PASSWORD) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SHOW GRANTS FOR '{READER_USER}'@'%'")
            grants = [next(iter(row.values())) for row in cursor.fetchall()]
    grant_text = "\n".join(str(item) for item in grants)
    grants_are_view_only = " ALL PRIVILEGES " not in grant_text and all(
        source not in grant_text
        for source in ("aishop_order`.*", "aishop_product`.*", "aishop_agent`.*")
    )
    checks = {
        "allTenViewsReadable": len(view_checks) == 10 and all(view_checks.values()),
        "allForbiddenOperationsDenied": all(denied_checks.values()),
        "grantsAreViewOnly": grants_are_view_only,
        "fixedCurrentDate": str(server["fixed_date"]) == "2026-08-27",
        "mysqlVersion": str(server["version"]).startswith("8.4.11"),
    }
    if not all(checks.values()):
        raise RuntimeError(
            f"Text2SQL fixture verification failed: checks={checks}, denied={denied_checks}, grants={grants}"
        )
    return {
        "verified": True,
        "checks": checks,
        "views": view_checks,
        "denied": denied_checks,
        "grants": grants,
        "server": {key: str(value) for key, value in server.items()},
        "catalogVersion": CATALOG_VERSION,
    }


def fingerprint() -> dict[str, Any]:
    files = [COMPOSE_FILE, ADMIN_MIGRATION, *(path for _, path in SERVICE_MIGRATIONS)]
    files.extend(sorted(SQL_DIR.glob("*.sql")))
    agent_migrations = sorted((AGENT_ROOT / "scripts/alembic/versions").glob("*.py"))
    files.extend(agent_migrations)
    return {
        "mysqlImage": "mysql:8.4.11",
        "redisImage": "redis:7.4.2-alpine",
        "fixedTimestamp": f"{FIXED_TIMESTAMP} Asia/Shanghai",
        "catalogVersion": CATALOG_VERSION,
        "files": {
            str(path.relative_to(BACKEND_ROOT)): sha256_file(path) for path in sorted(files)
        },
    }


def source_data_fingerprint() -> dict[str, Any]:
    """Hash all source-service base tables; Agent/Admin evidence tables are excluded."""
    tables: list[dict[str, Any]] = []
    with _mysql_connection(user="root", password=ROOT_PASSWORD) as connection:
        with connection.cursor() as cursor:
            for database in SOURCE_DATA_DATABASES:
                cursor.execute(
                    f"SHOW FULL TABLES FROM `{database}` WHERE Table_type='BASE TABLE'"
                )
                names = sorted(str(next(iter(row.values()))) for row in cursor.fetchall())
                for table in names:
                    cursor.execute(f"CHECKSUM TABLE `{database}`.`{table}`")
                    result = cursor.fetchone() or {}
                    checksum = result.get("Checksum")
                    tables.append(
                        {
                            "database": database,
                            "table": table,
                            "checksum": None if checksum is None else int(checksum),
                        }
                    )
    return {
        "schemaVersion": "aishop-text2sql-source-data-fingerprint/v0",
        "databases": list(SOURCE_DATA_DATABASES),
        "tableCount": len(tables),
        "sha256": sha256_bytes(canonical_json_bytes(tables)),
        "tables": tables,
    }


def _preferred_column_type(column: str, branch_view: str) -> dict[str, Any] | None:
    view = build_catalog()["views"][branch_view]
    spec = (view.get("columns") or {}).get(column)
    if not isinstance(spec, dict):
        return None
    return {
        key: spec[key]
        for key in ("type", "unit", "scale", "displayScale", "nullable")
        if key in spec
    }


def _inferred_column_type(description: tuple[Any, ...]) -> dict[str, Any]:
    type_code = description[1]
    if type_code in {FIELD_TYPE.DECIMAL, FIELD_TYPE.NEWDECIMAL, FIELD_TYPE.FLOAT, FIELD_TYPE.DOUBLE}:
        result: dict[str, Any] = {"type": "DECIMAL"}
        if len(description) > 5 and isinstance(description[5], int):
            result["scale"] = description[5]
        return result
    if type_code in {
        FIELD_TYPE.TINY,
        FIELD_TYPE.SHORT,
        FIELD_TYPE.LONG,
        FIELD_TYPE.LONGLONG,
        FIELD_TYPE.INT24,
        FIELD_TYPE.YEAR,
    }:
        return {"type": "INTEGER"}
    if type_code in {FIELD_TYPE.DATE, FIELD_TYPE.NEWDATE}:
        return {"type": "DATE"}
    if type_code in {FIELD_TYPE.DATETIME, FIELD_TYPE.TIMESTAMP}:
        return {"type": "DATETIME"}
    return {"type": "STRING"}


def _serialize_cell(value: Any, column_type: dict[str, Any]) -> Any:
    if value is None:
        return None
    kind = str(column_type.get("type") or "").upper()
    if kind == "DECIMAL":
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        scale = column_type.get("displayScale", column_type.get("scale"))
        if isinstance(scale, int):
            quantum = Decimal(1).scaleb(-scale)
            decimal_value = decimal_value.quantize(quantum, rounding=ROUND_HALF_UP)
        return format(decimal_value, "f")
    if kind == "INTEGER":
        return int(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value) if kind in {"STRING", "ENUM", "DATE", "DATETIME"} else value


def _query_oracle(cursor: Any, sql: str, branch_view: str) -> ResultOracle:
    cursor.execute(sql)
    raw_rows = cursor.fetchall()
    descriptions = list(cursor.description or [])
    columns = [str(item[0]) for item in descriptions]
    column_types: dict[str, dict[str, Any]] = {}
    for column, description in zip(columns, descriptions, strict=True):
        column_types[column] = _preferred_column_type(column, branch_view) or _inferred_column_type(
            description
        )
    rows = [
        {
            column: _serialize_cell(row.get(column), column_types[column])
            for column in columns
        }
        for row in raw_rows
    ]
    return ResultOracle(
        mode="EXACT_ROWS" if rows else "EMPTY_ROWS",
        columns=columns,
        columnTypes=column_types,
        rows=rows,
        orderSensitive="ORDER BY" in sql.upper(),
        materialized=True,
    )


def materialize_oracles(
    dataset_path: Path = DEFAULT_DATASET, *, overwrite: bool = False
) -> dict[str, Any]:
    cases = load_cases(dataset_path)
    materialized: dict[str, Text2SqlCase] = {}
    for state in ("base", "boundary", "empty"):
        reset(state)
        with _mysql_connection(
            user=READER_USER, password=READER_PASSWORD, database="aishop_admin"
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cursor.execute("SET SESSION time_zone = '+08:00'")
                cursor.execute(f"SET SESSION timestamp = UNIX_TIMESTAMP('{FIXED_TIMESTAMP}')")
                for case in (item for item in cases if item.fixture_state == state):
                    if not case.expected.reference_sql:
                        materialized[case.case_id] = case
                        continue
                    cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                    try:
                        oracles = [
                            _query_oracle(cursor, sql, branch.semantic_view)
                            for sql, branch in zip(
                                case.expected.reference_sql,
                                case.expected.branches,
                                strict=True,
                            )
                        ]
                    finally:
                        cursor.execute("ROLLBACK")
                    public = case.public()
                    public["expected"]["branchResultOracles"] = [
                        oracle.model_dump(by_alias=True, mode="json") for oracle in oracles
                    ]
                    public["expected"]["resultOracle"] = oracles[0].model_dump(
                        by_alias=True, mode="json"
                    )
                    materialized[case.case_id] = Text2SqlCase.model_validate(public)
    ordered = [materialized[case.case_id] for case in cases]
    summary = validate_v0(ordered)
    if any(
        case.expected.reference_sql
        and not all(oracle.materialized for oracle in case.expected.branch_result_oracles)
        for case in ordered
    ):
        raise RuntimeError("not every ANSWER branch received a materialized oracle")
    output = dataset_path if overwrite else dataset_path.with_name(f"{dataset_path.stem}.materialized.jsonl")
    write_cases(output, ordered, overwrite=overwrite)
    return {"output": str(output), "summary": summary}


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
