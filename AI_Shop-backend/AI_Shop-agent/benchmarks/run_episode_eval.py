"""Deterministic order-aftersales Episode quality gate.

This benchmark evaluates only persisted facts. It does not call an LLM, export
training data, or turn transaction outcomes into a scalar reward.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.episode_evaluator import (  # noqa: E402
    evaluate_order_aftersales_episode,
    recursive_subset_match,
)

DEFAULT_DATASET = Path(__file__).with_name("order_aftersales_episode_v1.jsonl")
DEFAULT_LOCK = Path(__file__).with_name("order_aftersales_episode_v1.lock.json")


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("Episode eval rows must be JSON objects")
        cases.append(value)
    return cases


def validate_lock(cases: list[dict[str, Any]], dataset: Path, lock_path: Path) -> dict[str, Any]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
    if lock.get("schemaVersion") != 1:
        errors.append("unsupported lock schema")
    if digest != lock.get("datasetSha256"):
        errors.append("dataset SHA does not match lock")
    if len(cases) != int(lock.get("caseCount") or 0):
        errors.append("case count does not match lock")
    ids = [str(case.get("id") or "") for case in cases]
    if "" in ids or len(ids) != len(set(ids)):
        errors.append("case IDs must be non-empty and unique")
    for case in cases:
        if not isinstance(case.get("episode"), dict):
            errors.append(f"{case.get('id')} has no episode object")
        if not isinstance(case.get("expected"), dict):
            errors.append(f"{case.get('id')} has no expected object")
    expected_thresholds = {
        "passRate": 1.0,
        "reviewEligibleAccuracy": 1.0,
        "trainingEligibilityAccuracy": 1.0,
    }
    if lock.get("thresholds") != expected_thresholds:
        errors.append(f"thresholds must remain frozen at {expected_thresholds}")
    if errors:
        raise ValueError("Episode eval contract invalid:\n- " + "\n- ".join(errors))
    return {"datasetSha256": digest, **lock}


def evaluate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    review_correct = 0
    review_graded = 0
    training_correct = 0
    training_graded = 0
    for case in cases:
        actual = evaluate_order_aftersales_episode(case["episode"])
        expected = case["expected"]
        mismatches = recursive_subset_match(expected, actual)
        if mismatches:
            failures.append(
                {
                    "id": case.get("id"),
                    "mismatches": mismatches,
                    "expected": expected,
                    "actual": actual,
                }
            )
        if "reviewEligible" in expected:
            review_graded += 1
            review_correct += int(expected["reviewEligible"] == actual["reviewEligible"])
        if "trainingEligible" in expected:
            training_graded += 1
            training_correct += int(expected["trainingEligible"] == actual["trainingEligible"])
    total = len(cases)
    return {
        "cases": total,
        "passed": total - len(failures),
        "passRate": round((total - len(failures)) / total, 4) if total else 0.0,
        "reviewEligibleAccuracy": round(review_correct / review_graded, 4)
        if review_graded
        else 0.0,
        "trainingEligibilityAccuracy": round(training_correct / training_graded, 4)
        if training_graded
        else 0.0,
        "failures": failures,
    }


def enforce_thresholds(summary: dict[str, Any], thresholds: dict[str, float]) -> None:
    failed = [
        f"{metric}={float(summary.get(metric) or 0):.4f} < {minimum:.4f}"
        for metric, minimum in thresholds.items()
        if float(summary.get(metric) or 0) < float(minimum)
    ]
    if failed:
        raise AssertionError("Episode eval threshold failed: " + "; ".join(failed))


def run(dataset: Path = DEFAULT_DATASET, lock_path: Path = DEFAULT_LOCK) -> dict[str, Any]:
    cases = load_cases(dataset)
    contract = validate_lock(cases, dataset, lock_path)
    summary = evaluate_cases(cases)
    enforce_thresholds(summary, contract["thresholds"])
    return {"contract": contract, "summary": summary}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    args = parser.parse_args()
    try:
        result = run(args.dataset, args.lock)
    except (ValueError, AssertionError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
