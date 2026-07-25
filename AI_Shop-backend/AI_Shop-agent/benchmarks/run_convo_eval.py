#!/usr/bin/env python3
"""跑冻结评测集并对 lock 里的基线做门禁。

    .venv/bin/python benchmarks/run_convo_eval.py
    .venv/bin/python benchmarks/run_convo_eval.py --write-results
    .venv/bin/python benchmarks/run_convo_eval.py --no-gate      # 只看数字
    .venv/bin/python benchmarks/run_convo_eval.py --bootstrap-lock  # 首次生成基线

``--bootstrap-lock`` 只该用一次。它把当前结果写成基线，包括当前的失败清单——
也就是"承认现在错这些"，不是"把错的改成对的"。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.convo_eval_dataset import (  # noqa: E402
    DATASET_PATH,
    DATASET_VERSION,
    LOCK_PATH,
    RESULTS_DIR,
    dataset_sha256,
    load_cases,
    load_lock,
)
from benchmarks.convo_eval_runner import run_all_sync, summarize  # noqa: E402

RUNNER_VERSION = "aishop_convo_eval_runner_v1"


def check_gate(summary: dict, lock: dict, actual_sha: str) -> list[str]:
    """返回门禁问题清单，空表示通过。

    两个方向都算失败：变差是回归，变好但没更新 lock 说明 lock 已经不再是事实。
    """
    problems: list[str] = []

    expected_sha = lock.get("datasetSha256")
    if expected_sha != actual_sha:
        problems.append(
            f"数据集 SHA-256 不匹配：lock={expected_sha} 实际={actual_sha}。"
            "改了题面就要同步 lock，冻结集不能被静默修改。"
        )
        # 题面都不是同一份了，后面比指标没有意义。
        return problems

    known = set(lock.get("knownFailures", {}))
    actual_failed = set(summary["failedIds"])

    regressions = sorted(actual_failed - known)
    if regressions:
        problems.append(f"出现基线里没有的失败（回归）：{regressions}")

    fixed = sorted(known - actual_failed)
    if fixed:
        problems.append(
            f"这些已知失败已经修好了，但还挂在 lock 的 knownFailures 里：{fixed}。"
            "把它们从 lock 和 KNOWN_LIMITATIONS.md 里删掉——lock 记的是当前事实。"
        )

    baseline = lock.get("baseline", {})
    for split in ("dev", "test"):
        floor = baseline.get("bySplit", {}).get(split, {}).get("passRate")
        actual = summary["bySplit"][split]["passRate"]
        if floor is not None and actual < floor:
            problems.append(f"{split} 通过率 {actual} 低于基线 {floor}")
    for name, stats in baseline.get("byDimension", {}).items():
        floor = stats.get("passRate")
        actual = summary["byDimension"].get(name, {}).get("passRate")
        if floor is not None and actual is not None and actual < floor:
            problems.append(f"维度 {name} 通过率 {actual} 低于基线 {floor}")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-results", action="store_true", help="把结果落到 results/")
    parser.add_argument("--no-gate", action="store_true", help="只打印指标，不做门禁")
    parser.add_argument(
        "--bootstrap-lock",
        action="store_true",
        help="用当前结果生成/覆盖 lock 基线，只在首次或有意重置基线时用",
    )
    args = parser.parse_args()

    cases = load_cases()
    actual_sha = dataset_sha256()
    report = run_all_sync(cases)
    summary = summarize(report)
    summary = {
        "datasetVersion": DATASET_VERSION,
        "runnerVersion": RUNNER_VERSION,
        "datasetSha256": actual_sha,
        "mode": "offline_deterministic",
        **summary,
    }

    if args.bootstrap_lock:
        lock = {
            "datasetVersion": DATASET_VERSION,
            "datasetSha256": actual_sha,
            "runnerVersion": RUNNER_VERSION,
            "frozenPolicy": {
                "resampling": "forbidden",
                "relabeling": "forbidden",
                "note": "期望值按正确的客服行为写；跑出来错的留在集合里并记入 KNOWN_LIMITATIONS.md",
            },
            "baseline": {
                "cases": summary["cases"],
                "passed": summary["passed"],
                "passRate": summary["passRate"],
                "bySplit": summary["bySplit"],
                "bySubset": summary["bySubset"],
                "byDimension": summary["byDimension"],
            },
            "knownFailures": {
                o.id: {
                    "subset": o.subset,
                    "split": o.split,
                    "dimensions": o.failed_dimensions,
                    "note": o.note,
                    "actual": o.actual,
                }
                for o in report.failures
            },
        }
        LOCK_PATH.write_text(
            json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        print(f"已写入基线 {LOCK_PATH.name}：{summary['passed']}/{summary['cases']} 通过，"
              f"{len(report.failures)} 条已知失败")

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.write_results:
        RESULTS_DIR.mkdir(exist_ok=True)
        (RESULTS_DIR / "convo_eval_v1_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        with (RESULTS_DIR / "convo_eval_v1_raw.jsonl").open("w", encoding="utf-8") as fh:
            for outcome in report.outcomes:
                fh.write(json.dumps(asdict(outcome), ensure_ascii=False) + "\n")
        with (RESULTS_DIR / "convo_eval_v1_failures.jsonl").open("w", encoding="utf-8") as fh:
            for outcome in report.failures:
                row = asdict(outcome)
                row["failedDimensions"] = outcome.failed_dimensions
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"结果已写入 {RESULTS_DIR}")

    if report.failures:
        print(f"\n失败 {len(report.failures)} 条：", file=sys.stderr)
        for outcome in report.failures:
            print(
                f"  {outcome.id} [{outcome.subset}/{outcome.split}] "
                f"维度={outcome.failed_dimensions} 实际={outcome.actual} — {outcome.note}",
                file=sys.stderr,
            )

    if args.no_gate or args.bootstrap_lock:
        return 0

    if not LOCK_PATH.exists():
        print(f"\n缺 {LOCK_PATH.name}，先跑 --bootstrap-lock", file=sys.stderr)
        return 1

    problems = check_gate(summary, load_lock(), actual_sha)
    if problems:
        print("\n门禁未通过：", file=sys.stderr)
        for p in problems:
            print(f"  [FAIL] {p}", file=sys.stderr)
        return 1
    print(f"\n门禁通过：{DATASET_PATH.name} 与基线一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
