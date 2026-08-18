"""Suite registry for the one public evaluation CLI."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARKS_ROOT = PROJECT_ROOT / "benchmarks"
SUITES_ROOT = BENCHMARKS_ROOT / "suites"


@dataclass(frozen=True)
class SuiteDefinition:
    suite_id: str
    contract: dict[str, Any]
    path: Path

    @property
    def result_root(self) -> Path:
        return PROJECT_ROOT / str(
            self.contract.get("resultRoot") or "benchmarks/results"
        )

    @property
    def run_id_pattern(self) -> re.Pattern[str]:
        return re.compile(str(self.contract.get("runIdPattern") or r".+"))

    @property
    def adapter(self) -> str:
        return str(self.contract.get("adapter") or self.suite_id)

    @property
    def stages(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.contract.get("stages") or ())


def load_suite(suite_id: str) -> SuiteDefinition:
    normalized = str(suite_id or "").strip()
    aliases = {
        "rag-v5-retrieval": "rag-v5",
        "rag-v5-generation": "rag-v5",
    }
    normalized = aliases.get(normalized, normalized)
    if not re.fullmatch(r"[a-z0-9-]+", normalized):
        raise ValueError(f"invalid suite id: {suite_id!r}")
    path = SUITES_ROOT / f"{normalized}.json"
    if not path.is_file():
        raise ValueError(f"unknown evaluation suite: {normalized}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("suiteId") != normalized:
        raise ValueError(f"suite contract identity mismatch: {path}")
    if payload.get("runner") != "benchmarks/eval.py":
        raise ValueError(f"suite is not registered with the unified runner: {normalized}")
    if not payload.get("adapter") or not payload.get("stages"):
        raise ValueError(f"suite contract lacks adapter/stages: {normalized}")
    if any(not isinstance(stage, str) or not stage for stage in payload["stages"]):
        raise ValueError(f"suite contract contains an invalid stage: {normalized}")
    return SuiteDefinition(normalized, payload, path)


def list_suites() -> list[SuiteDefinition]:
    suites: list[SuiteDefinition] = []
    for path in sorted(SUITES_ROOT.glob("*.json")):
        if path.stem.startswith("baseline-"):
            continue
        suite = load_suite(path.stem)
        if suite.contract.get("hiddenFromFormalList"):
            continue
        suites.append(suite)
    return suites
