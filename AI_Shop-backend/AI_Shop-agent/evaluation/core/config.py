from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from evaluation import CURRENT_SCHEMA_VERSION, SCHEMA_VERSION
from evaluation.core.contracts import ValidationError
from evaluation.core.io import EVALUATION_ROOT, load_json

SUITE_PATH = EVALUATION_ROOT / "suite.json"


@lru_cache(maxsize=1)
def load_suite(path: Path = SUITE_PATH) -> dict[str, Any]:
    value = load_json(path)
    if not isinstance(value, dict):
        raise ValidationError("suite.json must contain an object")
    if value.get("schemaVersion") not in {SCHEMA_VERSION, CURRENT_SCHEMA_VERSION}:
        raise ValidationError(
            f"suite schema must be {SCHEMA_VERSION} or {CURRENT_SCHEMA_VERSION}, "
            f"got {value.get('schemaVersion')!r}"
        )
    domains = value.get("domains")
    if not isinstance(domains, dict) or set(domains) != {"search", "rag", "agent"}:
        raise ValidationError("suite domains must be exactly search, rag, and agent")
    for domain, config in domains.items():
        metrics = config.get("metrics") if isinstance(config, dict) else None
        if not isinstance(metrics, dict) or not metrics:
            raise ValidationError(f"suite domain {domain} has no metric contracts")
        gates = config.get("gates")
        if not isinstance(gates, list) or not gates:
            raise ValidationError(f"suite domain {domain} has no hard gates")
        for gate in gates:
            if gate.get("metric") not in metrics:
                raise ValidationError(
                    f"suite domain {domain} gate references unknown metric {gate.get('metric')}"
                )
            if gate.get("operator") not in {">=", "<=", "=="}:
                raise ValidationError(f"unsupported gate operator: {gate.get('operator')}")
            if gate.get("field", "value") not in {"value", "lower", "upper"}:
                raise ValidationError(f"unsupported gate field: {gate.get('field')}")
    return value
