from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("blackbox_pilot.py")
SPEC = importlib.util.spec_from_file_location("blackbox_pilot", MODULE_PATH)
assert SPEC and SPEC.loader
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


def _report(actor: str, passed: int) -> dict:
    rows = [
        {"taskId": task["id"], "passed": index < passed}
        for index, task in enumerate(pilot.TASKS)
    ]
    return {"actorLabel": actor, "taskResults": rows}


def test_task_card_contains_six_url_only_tasks(tmp_path: Path) -> None:
    path = tmp_path / "task-card.md"
    pilot._write_task_card(path, "http://127.0.0.1:6101")
    text = path.read_text(encoding="utf-8")
    assert len(pilot.TASKS) == 6
    assert all(task["id"] in text and task["message"] in text for task in pilot.TASKS)
    assert "禁止读取仓库" in text


def test_aggregate_requires_two_actors_and_six_sessions(tmp_path: Path) -> None:
    for actor in ("model-a", "model-b"):
        for session in range(3):
            directory = tmp_path / f"{actor}-{session}"
            directory.mkdir()
            (directory / "result.json").write_text(
                json.dumps(_report(actor, 6), ensure_ascii=False), encoding="utf-8"
            )

    pilot.aggregate(tmp_path)

    report = json.loads((tmp_path / "synthetic-blackbox-report.json").read_text())
    assert report["status"] == "PASS"
    assert report["taskSuccessCount"] == 36
    assert report["realUserStatus"] == "NOT_COLLECTED"


def test_aggregate_fails_when_a_critical_task_misses_threshold(tmp_path: Path) -> None:
    for actor in ("model-a", "model-b"):
        for session in range(3):
            rows = [{"taskId": task["id"], "passed": True} for task in pilot.TASKS]
            if session > 0:
                next(row for row in rows if row["taskId"] == "TX-01")["passed"] = False
            directory = tmp_path / f"{actor}-{session}"
            directory.mkdir()
            (directory / "result.json").write_text(
                json.dumps({"actorLabel": actor, "taskResults": rows}), encoding="utf-8"
            )

    pilot.aggregate(tmp_path)

    report = json.loads((tmp_path / "synthetic-blackbox-report.json").read_text())
    assert report["status"] == "FAIL"
    assert report["taskSuccess"]["TX-01"] == 2
