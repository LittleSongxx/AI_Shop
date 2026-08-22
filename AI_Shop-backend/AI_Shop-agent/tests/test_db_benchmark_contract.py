from __future__ import annotations

from types import SimpleNamespace

import pytest

import evaluation.db_benchmark as db_benchmark


def test_benchmark_identifier_is_fail_closed() -> None:
    assert db_benchmark._quote_identifier("aishop_bench_123") == chr(96) + "aishop_bench_123" + chr(96)
    for value in ("bad-name", "bad" + chr(96) + "name", "db.table", "", "1;DROP DATABASE x"):
        with pytest.raises(ValueError, match="unsafe MySQL identifier"):
            db_benchmark._quote_identifier(value)


def test_isolated_benchmark_requires_root_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MYSQL_ROOT_PASSWORD", raising=False)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="MYSQL_ROOT_PASSWORD is required"):
            await db_benchmark._open_dedicated_database()

    import asyncio

    asyncio.run(run())


def test_benchmark_records_dropped_lifecycle_after_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDatabase:
        facts = SimpleNamespace(
            database_name="aishop_bench_test",
            source_database_name="aishop",
            dedicated=True,
            lifecycle="CREATED_AND_PENDING_CLEANUP",
            source_snapshot_sha256="b" * 64,
            copied_rows={"agent_final_offer_snapshot": 1},
        )

    dropped: list[FakeDatabase] = []

    async def fake_open() -> FakeDatabase:
        return FakeDatabase()

    async def fake_drop(db: FakeDatabase) -> None:
        dropped.append(db)

    async def fake_facts(db: FakeDatabase | None = None) -> dict[str, object]:
        return {
            "databaseName": "aishop_bench_test",
            "sourceDatabaseName": "aishop",
            "serverVersion": "8.4",
            "transactionIsolation": "REPEATABLE-READ",
            "endpoint": "127.0.0.1:3306",
            "dedicatedBenchmarkDatabase": True,
            "databaseLifecycle": db.facts.lifecycle if db else None,
        }

    async def fake_rollback(db: object | None = None) -> dict[str, object]:
        return {"passed": True, "committedWrites": 0}

    async def fake_measure(operation: object, product_ids: object, *, iterations: int) -> dict[str, object]:
        return {
            "iterations": iterations,
            "successfulIterations": iterations,
            "roundTrips": 1,
            "roundTripsPerSuccessfulIteration": [1] * iterations,
            "totalRoundTrips": iterations,
            "connectionAcquisitions": 1,
            "connectionAcquisitionsPerSuccessfulIteration": [1] * iterations,
            "totalConnectionAcquisitions": iterations,
            "returnedRowsPerSuccessfulIteration": [0] * iterations,
            "resultSha256PerSuccessfulIteration": ["c" * 64] * iterations,
            "stableResult": True,
            "errorRate": 0,
            "counterSource": "test",
            "p50Ms": 1.0,
            "p95Ms": 1.0,
            "latenciesMs": [1.0] * iterations,
        }

    monkeypatch.setattr(db_benchmark, "_open_dedicated_database", fake_open)
    monkeypatch.setattr(db_benchmark, "_drop_dedicated_database", fake_drop)
    monkeypatch.setattr(db_benchmark, "_database_facts", fake_facts)
    monkeypatch.setattr(db_benchmark, "_rollback_probe", fake_rollback)
    monkeypatch.setattr(db_benchmark, "_measure", fake_measure)
    monkeypatch.setattr(
        db_benchmark,
        "_candidate_selection",
        lambda size: {
            "ids": [f"p-{index}" for index in range(size)],
            "candidateCount": size,
            "uniqueCandidateCount": size,
        },
    )

    import asyncio

    result = asyncio.run(db_benchmark.benchmark_db_sizes([1], iterations=1, isolated=True))
    assert result["databaseFacts"]["databaseLifecycle"] == "CREATED_AND_DROPPED"
    assert len(dropped) == 1


def test_shared_benchmark_does_not_claim_dedicated_database(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_facts(db: object | None = None) -> dict[str, object]:
        return {
            "databaseName": "aishop",
            "serverVersion": "8.4",
            "transactionIsolation": "REPEATABLE-READ",
            "endpoint": "127.0.0.1:3306",
            "dedicatedBenchmarkDatabase": False,
        }

    async def fake_rollback(db: object | None = None) -> dict[str, object]:
        return {"passed": True, "committedWrites": 0}

    async def fake_measure(operation: object, product_ids: object, *, iterations: int) -> dict[str, object]:
        digest = "d" * 64
        return {
            "iterations": iterations,
            "successfulIterations": iterations,
            "roundTrips": 1,
            "roundTripsPerSuccessfulIteration": [1] * iterations,
            "totalRoundTrips": iterations,
            "connectionAcquisitions": 1,
            "connectionAcquisitionsPerSuccessfulIteration": [1] * iterations,
            "totalConnectionAcquisitions": iterations,
            "returnedRowsPerSuccessfulIteration": [0] * iterations,
            "resultSha256PerSuccessfulIteration": [digest] * iterations,
            "stableResult": True,
            "errorRate": 0,
            "counterSource": "test",
            "p50Ms": 1.0,
            "p95Ms": 1.0,
            "latenciesMs": [1.0] * iterations,
        }

    monkeypatch.setattr(db_benchmark, "_database_facts", fake_facts)
    monkeypatch.setattr(db_benchmark, "_rollback_probe", fake_rollback)
    monkeypatch.setattr(db_benchmark, "_measure", fake_measure)
    monkeypatch.setattr(
        db_benchmark,
        "_candidate_selection",
        lambda size: {
            "ids": [f"p-{index}" for index in range(size)],
            "candidateCount": size,
            "uniqueCandidateCount": size,
        },
    )

    import asyncio

    result = asyncio.run(db_benchmark.benchmark_db_sizes([1], iterations=1, isolated=False))
    assert result["databaseFacts"]["dedicatedBenchmarkDatabase"] is False
    assert "databaseLifecycle" not in result["databaseFacts"]
