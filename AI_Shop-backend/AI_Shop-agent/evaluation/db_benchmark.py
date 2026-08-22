"""Isolated read-only DB benchmark for batch versus N+1 feature access.

The benchmark reports local query evidence only.  It intentionally does not
reuse quality-run denominators or call its timings a production SLO.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiomysql

from app.config.settings import get_settings
from app.db.pool import acquire as app_acquire
from app.db.pool import transaction as app_transaction
from evaluation.core.catalog import load_catalog_fixture
from evaluation.core.fingerprints import source_fingerprint
from evaluation.core.io import (
    EVIDENCE_ROOT,
    atomic_write_json,
    atomic_write_text,
    sha256_file,
    utc_now,
)
from evaluation.core.metrics import percentile

DB_BENCHMARK_ROOT = EVIDENCE_ROOT.parent / "benchmarks" / "db"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")
_BENCHMARK_TABLES = (
    "agent_final_offer_snapshot",
    "agent_product_decision_feature",
)

# Keep the historical module-level names available for focused tests and
# downstream diagnostics that monkeypatch the application pool boundary. The
# isolated path never uses these aliases; it receives its own pool explicitly.
acquire = app_acquire
transaction = app_transaction


@dataclass(frozen=True)
class QueryObservation:
    round_trips: int
    connection_acquisitions: int
    returned_rows: int
    result_sha256: str


@dataclass(frozen=True)
class BenchmarkDatabaseFacts:
    """Connection and copy facts for the isolated benchmark schema."""

    database_name: str
    source_database_name: str
    dedicated: bool
    lifecycle: str
    source_snapshot_sha256: str | None = None
    copied_rows: dict[str, int] | None = None


class _BenchmarkDatabase:
    """Small dedicated pool wrapper used only by the DB benchmark."""

    def __init__(self, pool: aiomysql.Pool, facts: BenchmarkDatabaseFacts) -> None:
        self.pool = pool
        self.facts = facts

    async def close(self) -> None:
        self.pool.close()
        await self.pool.wait_closed()


@asynccontextmanager
async def _cursor(db: _BenchmarkDatabase | None = None):
    """Use the isolated pool when supplied, otherwise preserve unit-test hooks."""

    if db is None:
        async with acquire() as cursor:
            yield cursor
        return
    async with db.pool.acquire() as connection:
        async with connection.cursor(aiomysql.DictCursor) as cursor:
            yield cursor


@asynccontextmanager
async def _transaction(db: _BenchmarkDatabase | None = None):
    if db is None:
        async with transaction() as cursor:
            yield cursor
        return
    async with db.pool.acquire() as connection:
        await connection.begin()
        try:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                yield cursor
        except BaseException:
            await connection.rollback()
            raise
        else:
            await connection.commit()


def _rows_digest(rows: Sequence[Any]) -> str:
    encoded = sorted(
        json.dumps(row, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        for row in rows
    )
    return hashlib.sha256("\n".join(encoded).encode("utf-8")).hexdigest()


def _candidate_selection(size: int) -> dict[str, Any]:
    catalog = load_catalog_fixture()
    values = list(
        dict.fromkeys(
            str(item.get("productId") or "")
            for item in catalog.get("products") or []
            if str(item.get("productId") or "")
        )
    )
    if not values:
        raise ValueError("catalog fixture has no product IDs")
    known = values[:size]
    generated = [
        f"__aishop_benchmark_nonfixture_{index:04d}"
        for index in range(size - len(known))
    ]
    selected = [*known, *generated]
    if len(selected) != len(set(selected)):
        raise AssertionError("benchmark candidate IDs must be unique")
    return {
        "ids": selected,
        "candidateCount": size,
        "uniqueCandidateCount": len(set(selected)),
        "catalogFixtureCandidateCount": len(known),
        "nonFixtureCandidateCount": len(generated),
        "selectionPolicy": "UNIQUE_CATALOG_IDS_THEN_EXPLICIT_NON_FIXTURE_IDS",
    }


async def _query_offer_batch(
    product_ids: Sequence[str], db: _BenchmarkDatabase | None = None
) -> QueryObservation:
    placeholders = ",".join(["%s"] * len(product_ids))
    async with _cursor(db) as cursor:
        await cursor.execute(
            "SELECT product_id, offer_json FROM ("
            "SELECT product_id, offer_json, "
            "ROW_NUMBER() OVER (PARTITION BY product_id "
            "ORDER BY created_at DESC, snapshot_id DESC) AS row_num "
            "FROM agent_final_offer_snapshot "
            f"WHERE product_id IN ({placeholders})"
            ") AS latest WHERE row_num=1",
            tuple(product_ids),
        )
        rows = await cursor.fetchall()
    return QueryObservation(1, 1, len(rows), _rows_digest(rows))


async def _query_offer_n_plus_one(
    product_ids: Sequence[str], db: _BenchmarkDatabase | None = None
) -> QueryObservation:
    returned_rows = 0
    rows: list[Any] = []
    async with _cursor(db) as cursor:
        for product_id in product_ids:
            await cursor.execute(
                "SELECT product_id, offer_json FROM agent_final_offer_snapshot "
                "WHERE product_id=%s ORDER BY created_at DESC, snapshot_id DESC LIMIT 1",
                (product_id,),
            )
            result = list(await cursor.fetchall())
            rows.extend(result)
            returned_rows += len(result)
    return QueryObservation(len(product_ids), 1, returned_rows, _rows_digest(rows))


async def _query_feature_batch(
    product_ids: Sequence[str], db: _BenchmarkDatabase | None = None
) -> QueryObservation:
    placeholders = ",".join(["%s"] * len(product_ids))
    async with _cursor(db) as cursor:
        await cursor.execute(
            f"SELECT product_id, feature_key, feature_value "
            f"FROM agent_product_decision_feature WHERE product_id IN ({placeholders}) "
            "AND review_status='VERIFIED' LIMIT %s",
            (*product_ids, max(40, len(product_ids) * 40)),
        )
        rows = await cursor.fetchall()
    return QueryObservation(1, 1, len(rows), _rows_digest(rows))


async def _query_feature_n_plus_one(
    product_ids: Sequence[str], db: _BenchmarkDatabase | None = None
) -> QueryObservation:
    returned_rows = 0
    rows: list[Any] = []
    async with _cursor(db) as cursor:
        for product_id in product_ids:
            await cursor.execute(
                "SELECT product_id, feature_key, feature_value "
                "FROM agent_product_decision_feature WHERE product_id=%s "
                "AND review_status='VERIFIED' LIMIT 40",
                (product_id,),
            )
            result = list(await cursor.fetchall())
            rows.extend(result)
            returned_rows += len(result)
    return QueryObservation(len(product_ids), 1, returned_rows, _rows_digest(rows))


async def _rollback_probe(db: _BenchmarkDatabase | None = None) -> dict[str, Any]:
    marker = f"__aishop_rollback_probe_{secrets.token_hex(12)}"
    params = (marker, "rollback-probe", "must-not-commit", "{}")
    before_count = 0
    inside_count = 0
    after_count = 0
    async with _cursor(db) as cursor:
        await cursor.execute(
            "SELECT COUNT(*) AS count FROM agent_product_decision_feature "
            "WHERE product_id=%s",
            (marker,),
        )
        before_count = int((await cursor.fetchone() or {}).get("count") or 0)
    if before_count != 0:
        raise RuntimeError("rollback probe marker unexpectedly exists before transaction")
    try:
        async with _transaction(db) as cursor:
            await cursor.execute(
                "INSERT INTO agent_product_decision_feature "
                "(product_id, feature_key, feature_value, source_type, evidence_json, "
                "confidence, review_status, version, valid_from, created_at, updated_at) "
                "VALUES (%s,%s,%s,'BENCHMARK',%s,1.0000,'DRAFT','rollback-v1',"
                "NOW(3),NOW(3),NOW(3))",
                params,
            )
            await cursor.execute(
                "SELECT COUNT(*) AS count FROM agent_product_decision_feature "
                "WHERE product_id=%s",
                (marker,),
            )
            inside_count = int((await cursor.fetchone() or {}).get("count") or 0)
            raise RuntimeError("benchmark rollback probe")
    except RuntimeError as exc:
        if str(exc) != "benchmark rollback probe":
            raise
    async with _cursor(db) as cursor:
        await cursor.execute(
            "SELECT COUNT(*) AS count FROM agent_product_decision_feature "
            "WHERE product_id=%s",
            (marker,),
        )
        after_count = int((await cursor.fetchone() or {}).get("count") or 0)
    return {
        "passed": before_count == 0 and inside_count == 1 and after_count == 0,
        "beforeCount": before_count,
        "insideTransactionCount": inside_count,
        "afterRollbackCount": after_count,
        "probeTable": "agent_product_decision_feature",
        "markerRedacted": True,
        "committedWrites": 0,
    }


async def _database_facts(db: _BenchmarkDatabase | None = None) -> dict[str, Any]:
    async with _cursor(db) as cursor:
        await cursor.execute(
            "SELECT DATABASE() AS databaseName, VERSION() AS serverVersion, "
            "@@transaction_isolation AS transactionIsolation"
        )
        row = await cursor.fetchone() or {}
    settings = get_settings()
    facts = {
        "databaseName": str(row.get("databaseName") or settings.mysql_database),
        "serverVersion": str(row.get("serverVersion") or "unknown"),
        "transactionIsolation": str(row.get("transactionIsolation") or "unknown"),
        "endpoint": f"{settings.mysql_host}:{settings.mysql_port}",
        "dedicatedBenchmarkDatabase": False,
    }
    if db is not None:
        facts.update(
            {
                "databaseName": db.facts.database_name,
                "sourceDatabaseName": db.facts.source_database_name,
                "dedicatedBenchmarkDatabase": db.facts.dedicated,
                "databaseLifecycle": db.facts.lifecycle,
                "sourceSnapshotSha256": db.facts.source_snapshot_sha256,
                "copiedRows": db.facts.copied_rows or {},
            }
        )
    return facts


async def _measure(
    operation: Any,
    product_ids: Sequence[str],
    *,
    iterations: int,
) -> dict[str, Any]:
    latencies: list[float] = []
    errors = 0
    observations: list[QueryObservation | None] = []
    for _ in range(iterations):
        started = time.perf_counter()
        try:
            observation = await operation(product_ids)
        except Exception:
            errors += 1
            observation = None
        observations.append(observation)
        latencies.append((time.perf_counter() - started) * 1000)
    successful = [item for item in observations if item is not None]
    round_trips = [item.round_trips for item in successful]
    connection_acquisitions = [item.connection_acquisitions for item in successful]
    returned_rows = [item.returned_rows for item in successful]
    result_digests = [item.result_sha256 for item in successful]
    return {
        "iterations": iterations,
        "successfulIterations": len(successful),
        "roundTrips": round_trips[0] if round_trips and len(set(round_trips)) == 1 else None,
        "roundTripsPerSuccessfulIteration": round_trips,
        "totalRoundTrips": sum(round_trips),
        "connectionAcquisitions": (
            connection_acquisitions[0]
            if connection_acquisitions and len(set(connection_acquisitions)) == 1
            else None
        ),
        "connectionAcquisitionsPerSuccessfulIteration": connection_acquisitions,
        "totalConnectionAcquisitions": sum(connection_acquisitions),
        "returnedRowsPerSuccessfulIteration": returned_rows,
        "resultSha256PerSuccessfulIteration": result_digests,
        "stableResult": bool(result_digests) and len(set(result_digests)) == 1,
        "counterSource": "COUNTED_CURSOR_EXECUTE_AND_POOL_ACQUIRE_CALLS",
        "p50Ms": round(percentile(latencies, 0.50), 3),
        "p95Ms": round(percentile(latencies, 0.95), 3),
        "errorRate": errors / iterations,
        "latenciesMs": [round(value, 3) for value in latencies],
    }


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"unsafe MySQL identifier: {value!r}")
    return chr(96) + value + chr(96)


async def _open_dedicated_database() -> _BenchmarkDatabase:
    """Create a disposable schema copied from the benchmark source tables."""

    settings = get_settings()
    root_user = os.getenv("MYSQL_ROOT_USER", "root").strip() or "root"
    root_password = os.getenv("MYSQL_ROOT_PASSWORD", "").strip()
    if not root_password:
        raise RuntimeError(
            "MYSQL_ROOT_PASSWORD is required for an isolated DB benchmark; "
            "use --shared only for an explicitly non-isolated diagnostic"
        )
    source_database = settings.mysql_database.strip()
    if not _IDENTIFIER_RE.fullmatch(source_database):
        raise ValueError("MYSQL_DATABASE is not a safe schema identifier")
    benchmark_database = f"aishop_bench_{secrets.token_hex(8)}"
    root_connection = await aiomysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=root_user,
        password=root_password,
        charset="utf8mb4",
        autocommit=True,
    )
    copied_rows: dict[str, int] = {}
    table_digests: dict[str, str] = {}
    try:
        async with root_connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                f"CREATE DATABASE {_quote_identifier(benchmark_database)} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
            )
            for table in _BENCHMARK_TABLES:
                source = f"{_quote_identifier(source_database)}.{_quote_identifier(table)}"
                target = f"{_quote_identifier(benchmark_database)}.{_quote_identifier(table)}"
                await cursor.execute(f"CREATE TABLE {target} LIKE {source}")
                await cursor.execute(f"SELECT * FROM {source}")
                source_digest = _rows_digest(await cursor.fetchall())
                await cursor.execute(f"INSERT INTO {target} SELECT * FROM {source}")
                await cursor.execute(f"SELECT COUNT(*) AS count FROM {target}")
                copied_rows[table] = int((await cursor.fetchone() or {}).get("count") or 0)
                await cursor.execute(f"SELECT * FROM {target}")
                target_digest = _rows_digest(await cursor.fetchall())
                if source_digest != target_digest:
                    raise RuntimeError(
                        f"benchmark source copy digest mismatch for table {table}"
                    )
                table_digests[table] = source_digest
    except BaseException:
        root_connection.close()
        try:
            cleanup_connection = await aiomysql.connect(
                host=settings.mysql_host,
                port=settings.mysql_port,
                user=root_user,
                password=root_password,
                charset="utf8mb4",
                autocommit=True,
            )
            async with cleanup_connection.cursor() as cursor:
                await cursor.execute(
                    f"DROP DATABASE IF EXISTS {_quote_identifier(benchmark_database)}"
                )
            cleanup_connection.close()
        except Exception:
            pass
        raise
    finally:
        root_connection.close()

    source_snapshot = hashlib.sha256(
        json.dumps(table_digests, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    pool = await aiomysql.create_pool(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=root_user,
        password=root_password,
        db=benchmark_database,
        charset="utf8mb4",
        autocommit=True,
        minsize=1,
        maxsize=max(2, min(settings.mysql_pool_max_size, 8)),
        pool_recycle=settings.mysql_pool_recycle_seconds,
    )
    return _BenchmarkDatabase(
        pool,
        BenchmarkDatabaseFacts(
            database_name=benchmark_database,
            source_database_name=source_database,
            dedicated=True,
            lifecycle="CREATED_AND_PENDING_CLEANUP",
            source_snapshot_sha256=source_snapshot,
            copied_rows=copied_rows,
        ),
    )


async def _drop_dedicated_database(db: _BenchmarkDatabase) -> None:
    settings = get_settings()
    root_user = os.getenv("MYSQL_ROOT_USER", "root").strip() or "root"
    root_password = os.getenv("MYSQL_ROOT_PASSWORD", "").strip()
    await db.close()
    connection = await aiomysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=root_user,
        password=root_password,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(
                f"DROP DATABASE IF EXISTS {_quote_identifier(db.facts.database_name)}"
            )
    finally:
        connection.close()


async def benchmark_db_sizes(
    sizes: Sequence[int] = (1, 10, 50, 100),
    *,
    iterations: int = 3,
    isolated: bool = True,
) -> dict[str, Any]:
    normalized = sorted({int(size) for size in sizes})
    if not normalized or any(size < 1 for size in normalized):
        raise ValueError("benchmark sizes must be positive")
    if not 1 <= iterations <= 20:
        raise ValueError("benchmark iterations must be between 1 and 20")
    db = await _open_dedicated_database() if isolated else None
    try:
        database_facts = await _database_facts(db)
        rollback_probe = await _rollback_probe(db)
        if not rollback_probe["passed"]:
            raise RuntimeError(f"database rollback probe failed: {rollback_probe}")
        rows: dict[str, Any] = {}
        for size in normalized:
            selection = _candidate_selection(size)
            ids = selection.pop("ids")
            batch_offer = await _measure(
                lambda values: _query_offer_batch(values, db),
                ids,
                iterations=iterations,
            )
            n_plus_one_offer = await _measure(
                lambda values: _query_offer_n_plus_one(values, db),
                ids,
                iterations=iterations,
            )
            batch_feature = await _measure(
                lambda values: _query_feature_batch(values, db),
                ids,
                iterations=iterations,
            )
            n_plus_one_feature = await _measure(
                lambda values: _query_feature_n_plus_one(values, db),
                ids,
                iterations=iterations,
            )

            def equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
                return (
                    left["errorRate"] == 0
                    and right["errorRate"] == 0
                    and left["stableResult"] is True
                    and right["stableResult"] is True
                    and left["resultSha256PerSuccessfulIteration"][0]
                    == right["resultSha256PerSuccessfulIteration"][0]
                )

            offer_equivalent = equivalent(batch_offer, n_plus_one_offer)
            feature_equivalent = equivalent(batch_feature, n_plus_one_feature)
            if not offer_equivalent or not feature_equivalent:
                raise RuntimeError(
                    "batch and N+1 benchmark result sets differ "
                    f"at candidate size {size}: offer={offer_equivalent}, "
                    f"feature={feature_equivalent}"
                )
            rows[str(size)] = {
                **selection,
                "batchOfferSnapshot": batch_offer,
                "nPlusOneOfferSnapshot": n_plus_one_offer,
                "batchDecisionFeature": batch_feature,
                "nPlusOneDecisionFeature": n_plus_one_feature,
                "resultEquivalence": {
                    "offerSnapshot": offer_equivalent,
                    "decisionFeature": feature_equivalent,
                    "method": "ORDER_INDEPENDENT_CANONICAL_ROW_SHA256",
                },
            }
        if isolated:
            # The finally block below drops the disposable schema before this
            # function returns. Evidence therefore records the completed
            # lifecycle, rather than the transient in-use state.
            database_facts["databaseLifecycle"] = "CREATED_AND_DROPPED"
        return {
            "schemaVersion": "aishop-db-benchmark/v2",
            "sizes": normalized,
            "rows": rows,
            "scope": "REAL_DEDICATED_DATABASE_READ_BENCHMARK_WITH_ROLLBACK_PROBE"
            if isolated
            else "REAL_LOCAL_DATABASE_READ_BENCHMARK_WITH_ROLLBACK_PROBE",
            "databaseFacts": database_facts,
            "rollbackProbe": rollback_probe,
            "notProductionSlo": True,
            "limitations": [
                "Local MySQL instance and copied dataset; no production capacity claim.",
                "The dedicated schema is disposable and copied from the local source at benchmark start.",
                "Round trips count cursor.execute calls and connection usage counts pool acquisitions in this runner.",
                "Candidate IDs are unique; sizes above the catalog count include explicitly labeled non-fixture misses.",
            ],
        }
    finally:
        if db is not None:
            await _drop_dedicated_database(db)


def _benchmark_file_inventory(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.iterdir())
        if path.is_file() and path.name not in {"SHA256SUMS", "evidence-manifest.json"}
    }


def _benchmark_report(result: dict[str, Any]) -> str:
    lines = [
        "# AI Shop DB batch benchmark",
        "",
        f"- Benchmark: {result['benchmarkId']}",
        f"- Created: {result['createdAt']}",
        "- Scope: real local database reads plus a rollback-only write probe",
        f"- Dedicated benchmark database: {result['databaseFacts']['dedicatedBenchmarkDatabase']}",
        "- Production SLO claim: none",
        "",
        "## Measurements",
        "",
    ]
    for size in result["sizes"]:
        row = result["rows"][str(size)]
        lines.append(f"### Candidate count {size}")
        for name in (
            "batchOfferSnapshot",
            "nPlusOneOfferSnapshot",
            "batchDecisionFeature",
            "nPlusOneDecisionFeature",
        ):
            measurement = row[name]
            lines.append(
                f"- {name}: roundTrips={measurement['roundTrips']}, "
                f"connectionAcquisitions={measurement['connectionAcquisitions']}, "
                f"P50={measurement['p50Ms']}ms, P95={measurement['p95Ms']}ms, "
                f"errorRate={measurement['errorRate']}"
            )
        lines.append(
            "- resultEquivalence: "
            f"offerSnapshot={row['resultEquivalence']['offerSnapshot']}, "
            f"decisionFeature={row['resultEquivalence']['decisionFeature']}, "
            f"method={row['resultEquivalence']['method']}"
        )
        lines.append("")
    lines.extend(
        [
            "## Transaction rollback probe",
            "",
            f"- passed={result['rollbackProbe']['passed']}",
            f"- beforeCount={result['rollbackProbe']['beforeCount']}",
            f"- insideTransactionCount={result['rollbackProbe']['insideTransactionCount']}",
            f"- afterRollbackCount={result['rollbackProbe']['afterRollbackCount']}",
            f"- committedWrites={result['rollbackProbe']['committedWrites']}",
            "",
            "## Interpretation boundary",
            "",
            "These timings describe this shared local development database, pool, schema, "
            "and candidate selection only. They are not production capacity, latency SLO, "
            "or a cross-region benchmark.",
            "",
        ]
    )
    return "\n".join(lines)


def write_db_benchmark_evidence(
    benchmark: dict[str, Any],
    *,
    benchmark_id: str,
) -> tuple[Path, str]:
    """Persist one immutable benchmark package outside quality-run denominators."""

    if not benchmark_id or any(char in benchmark_id for char in "/\\"):
        raise ValueError("benchmark_id must be a non-empty path-safe identifier")
    root = DB_BENCHMARK_ROOT / benchmark_id
    if root.exists():
        raise FileExistsError(f"benchmark evidence already exists: {root}")
    root.mkdir(parents=True)
    payload = {
        **benchmark,
        "benchmarkId": benchmark_id,
        "createdAt": utc_now(),
        "sourceFingerprint": source_fingerprint(),
    }
    atomic_write_json(root / "benchmark.json", payload, overwrite=False)
    atomic_write_text(root / "report.md", _benchmark_report(payload), overwrite=False)
    manifest = {
        "schemaVersion": "aishop-db-benchmark-evidence/v2",
        "benchmarkSchemaVersion": payload["schemaVersion"],
        "benchmarkId": benchmark_id,
        "createdAt": payload["createdAt"],
        "scope": payload["scope"],
        "notProductionSlo": bool(payload["notProductionSlo"]),
        "dedicatedBenchmarkDatabase": bool(
            payload["databaseFacts"]["dedicatedBenchmarkDatabase"]
        ),
        "rollbackProbePassed": bool(payload["rollbackProbe"]["passed"]),
        "sourceSha256": payload["sourceFingerprint"]["source"]["sha256"],
        "providerConfigurationSha256": payload["sourceFingerprint"][
            "providerConfigurationSha256"
        ],
        "files": _benchmark_file_inventory(root),
    }
    atomic_write_json(root / "evidence-manifest.json", manifest, overwrite=False)
    sums = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.iterdir())
        if path.is_file() and path.name != "SHA256SUMS"
    }
    atomic_write_text(
        root / "SHA256SUMS",
        "".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())),
        overwrite=False,
    )
    verify_db_benchmark_evidence(root)
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    return root, sha256_file(root / "SHA256SUMS")


def verify_db_benchmark_evidence(root: Path) -> dict[str, Any]:
    sums_path = root / "SHA256SUMS"
    if not root.is_dir() or not sums_path.is_file():
        raise ValueError(f"invalid DB benchmark evidence root: {root}")
    expected: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name or name in expected:
            raise ValueError(f"invalid DB benchmark SHA256SUMS line: {line!r}")
        expected[name] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(expected) != actual:
        raise ValueError("DB benchmark evidence file set differs from SHA256SUMS")
    for name, digest in expected.items():
        if sha256_file(root / name) != digest:
            raise ValueError(f"DB benchmark evidence hash mismatch: {name}")
    manifest_path = root / "evidence-manifest.json"
    benchmark_path = root / "benchmark.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != "aishop-db-benchmark-evidence/v2":
        raise ValueError("DB benchmark evidence schema is invalid")
    if benchmark.get("schemaVersion") != "aishop-db-benchmark/v2":
        raise ValueError("DB benchmark payload schema is invalid")
    if manifest.get("benchmarkSchemaVersion") != benchmark.get("schemaVersion"):
        raise ValueError("DB benchmark schema differs between manifest and payload")
    if manifest.get("benchmarkId") != benchmark.get("benchmarkId"):
        raise ValueError("DB benchmark ID differs between manifest and payload")
    source_facts = benchmark.get("sourceFingerprint") or {}
    if manifest.get("sourceSha256") != (source_facts.get("source") or {}).get("sha256"):
        raise ValueError("DB benchmark source fingerprint differs from manifest")
    if manifest.get("providerConfigurationSha256") != source_facts.get(
        "providerConfigurationSha256"
    ):
        raise ValueError("DB benchmark provider fingerprint differs from manifest")
    if benchmark.get("notProductionSlo") is not True:
        raise ValueError("DB benchmark must declare notProductionSlo=true")
    if benchmark.get("rollbackProbe", {}).get("passed") is not True:
        raise ValueError("DB benchmark rollback probe did not pass")
    if manifest.get("rollbackProbePassed") is not True:
        raise ValueError("DB benchmark manifest must record the rollback probe")
    dedicated = benchmark.get("databaseFacts", {}).get("dedicatedBenchmarkDatabase")
    if manifest.get("dedicatedBenchmarkDatabase") is not dedicated:
        raise ValueError("DB benchmark database-isolation claim differs from payload")
    if dedicated and benchmark.get("databaseFacts", {}).get("databaseLifecycle") != "CREATED_AND_DROPPED":
        raise ValueError(
            "dedicated DB benchmark must record CREATED_AND_DROPPED lifecycle"
        )
    inventory = _benchmark_file_inventory(root)
    if manifest.get("files") != inventory:
        raise ValueError("DB benchmark evidence inventory is stale")
    return {
        "verified": True,
        "root": str(root),
        "benchmarkId": benchmark.get("benchmarkId"),
        "sha256SumsSha256": sha256_file(sums_path),
    }
