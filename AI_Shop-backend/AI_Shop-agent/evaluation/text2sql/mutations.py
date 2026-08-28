from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from evaluation.text2sql.dataset import DEFAULT_DATASET, load_cases
from evaluation.text2sql.fixture import (
    FIXED_TIMESTAMP,
    READER_PASSWORD,
    READER_USER,
    _mysql_connection,
    _query_oracle,
    reset,
)
from evaluation.text2sql.io import canonical_json_bytes, utc_now, write_json


def generate_mutations(sql: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []

    def add(name: str, mutated: str) -> None:
        if mutated != sql and mutated not in {item[1] for item in candidates}:
            candidates.append((name, mutated))

    if re.search(r"\bSUM\s*\(", sql, flags=re.IGNORECASE):
        add("aggregation_sum_to_count", re.sub(r"\bSUM\s*\(", "COUNT(", sql, count=1, flags=re.IGNORECASE))
    if " DESC" in sql.upper():
        add("sort_desc_to_asc", re.sub(r"\bDESC\b", "ASC", sql, count=1, flags=re.IGNORECASE))
    elif " ASC" in sql.upper():
        add("sort_asc_to_desc", re.sub(r"\bASC\b", "DESC", sql, count=1, flags=re.IGNORECASE))
    add("threshold_lte_to_lt", re.sub(r"<=\s*0", "< 0", sql, count=1))
    add("threshold_low_stock_off_by_one", sql.replace("BETWEEN 1 AND 10", "BETWEEN 1 AND 9", 1))
    date_match = re.search(r"BETWEEN\s+'(2026-08-[0-9]{2})'\s+AND", sql, flags=re.IGNORECASE)
    if date_match:
        day = int(date_match.group(1)[-2:])
        if day < 27:
            shifted = f"2026-08-{day + 1:02d}"
            add("date_start_plus_one_day", sql[: date_match.start(1)] + shifted + sql[date_match.end(1) :])
    return candidates


def run_mutation_diagnostic(
    output: Path,
    *,
    dataset_path: Path = DEFAULT_DATASET,
) -> dict[str, Any]:
    cases = load_cases(dataset_path)
    results: list[dict[str, Any]] = []
    for state in ("base", "boundary", "empty"):
        reset(state)
        with _mysql_connection(
            user=READER_USER, password=READER_PASSWORD, database="aishop_admin"
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION time_zone = '+08:00'")
                cursor.execute(f"SET SESSION timestamp = UNIX_TIMESTAMP('{FIXED_TIMESTAMP}')")
                for case in (item for item in cases if item.fixture_state == state):
                    for branch, sql, oracle in zip(
                        case.expected.branches,
                        case.expected.reference_sql,
                        case.expected.branch_result_oracles,
                        strict=True,
                    ):
                        for mutation, mutated_sql in generate_mutations(sql):
                            try:
                                cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                                observed = _query_oracle(cursor, mutated_sql, branch.semantic_view)
                                query_error = None
                            except Exception as exc:  # diagnostic must retain invalid mutations
                                observed = None
                                query_error = f"{type(exc).__name__}: {exc}"
                            finally:
                                cursor.execute("ROLLBACK")
                            killed = observed is not None and canonical_json_bytes(
                                observed.rows
                            ) != canonical_json_bytes(oracle.rows)
                            results.append(
                                {
                                    "caseId": case.case_id,
                                    "branchId": branch.branch_id,
                                    "fixtureState": state,
                                    "mutation": mutation,
                                    "killed": killed,
                                    "invalidMutation": query_error is not None,
                                    "queryError": query_error,
                                    "mutatedSql": mutated_sql,
                                }
                            )
    valid = [item for item in results if not item["invalidMutation"]]
    report = {
        "schemaVersion": "aishop-text2sql-mutation-diagnostic/v0",
        "createdAt": utc_now(),
        "development": True,
        "provisional": True,
        "unseen": False,
        "releaseGateEligible": False,
        "mutationCount": len(results),
        "validMutationCount": len(valid),
        "killedMutationCount": sum(bool(item["killed"]) for item in valid),
        "mutationKillRate": (
            sum(bool(item["killed"]) for item in valid) / len(valid) if valid else None
        ),
        "results": results,
    }
    write_json(output, report)
    return report
