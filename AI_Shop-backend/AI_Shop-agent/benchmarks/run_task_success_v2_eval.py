#!/usr/bin/env python3
"""Run the frozen Agent v2 suite against the real AI_Shop serving stack.

The v2 suite keeps the 37 v1 single-turn cases unchanged and adds seven
stateful commerce sequences. Sequence actions use only existing public or
internal authenticated endpoints. Runtime identifiers remain in memory and
are never written back into the frozen dataset or the evidence report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.pool import acquire, close_pool, init_pool  # noqa: E402
from app.services.episode_query_service import episode_query_service  # noqa: E402
from app.services.message_service import agent_message_service  # noqa: E402
from app.services.pending_action_store import pending_action_store  # noqa: E402
from app.services.shopping_mission_service import shopping_mission_service  # noqa: E402
from app.services.shopping_profile_service import shopping_profile_service  # noqa: E402
from benchmarks import run_task_success_eval as v1  # noqa: E402

SUITE = "agent-v2"
EXECUTION_MODE = "LIVE_FULL_STACK"
DEFAULT_DATASET = Path(__file__).with_name("task_success_v2.jsonl")
DEFAULT_LOCK = Path(__file__).with_name("task_success_v2.lock.json")
RESULTS_ROOT = Path(__file__).with_name("results") / "task-success-live-v2"
ALLOWED_ACTIONS = frozenset(
    {
        "sendMessage",
        "selectVisualSubject",
        "reportClick",
        "addToCart",
        "createOrder",
        "markPaymentSuccess",
        "requestRefund",
        "postReview",
        "confirmAgentAction",
        "assertState",
    }
)
HTTP_ACTIONS = ALLOWED_ACTIONS - {"assertState"}
_JSON_FIELDS = frozenset(
    {
        "bizData",
        "productIds",
        "sourceRefs",
        "profile_json",
        "mission_json",
        "offer_json",
        "payload_json",
        "subjects_json",
    }
)


class EvaluationContractError(ValueError):
    """The v2 frozen data, private bindings, or runtime contract is invalid."""


def load_cases(path: Path) -> list[dict[str, Any]]:
    return v1.load_cases(path)


def dataset_sha256(path: Path) -> str:
    return v1.dataset_sha256(path)


def _load_json(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationContractError(f"cannot read {description}: {path}") from exc
    if not isinstance(value, dict):
        raise EvaluationContractError(f"{description} must be a JSON object: {path}")
    return value


def validate_contract(
    cases: list[dict[str, Any]], dataset: Path, lock_path: Path
) -> dict[str, Any]:
    lock = _load_json(lock_path, "dataset lock")
    errors: list[str] = []
    digest = dataset_sha256(dataset)
    known = [case for case in cases if case.get("kind", "single_turn") == "single_turn"]
    sequences = [case for case in cases if case.get("kind") == "sequence"]
    historical = v1.load_cases(v1.DEFAULT_DATASET)

    if lock.get("schemaVersion") != 3:
        errors.append("lock schemaVersion must be 3")
    if lock.get("suite") != SUITE:
        errors.append(f"lock suite must be {SUITE}")
    if lock.get("datasetSha256") != digest:
        errors.append("dataset SHA-256 differs from the frozen lock")
    if lock.get("knownDatasetSha256") != v1.dataset_sha256(v1.DEFAULT_DATASET):
        errors.append("knownDatasetSha256 does not bind the immutable v1 dataset")
    if known != historical or cases[:37] != historical:
        errors.append("the first 37 known single-turn cases must equal task_success_v1")

    counts = lock.get("caseCounts") or {}
    expected_counts = {"knownSingleTurn": 37, "sequence": 7, "total": 44}
    if counts != expected_counts:
        errors.append(f"lock caseCounts must equal {expected_counts}")
    if len(known) != 37 or len(sequences) != 7 or len(cases) != 44:
        errors.append("dataset must contain 37 known single-turn and 7 sequence cases")

    ids = [str(case.get("id") or "") for case in cases]
    if not all(ids) or len(ids) != len(set(ids)):
        errors.append("case IDs must be non-empty and unique")

    observed_actions: set[str] = set()
    sequence_ids: list[str] = []
    for case in sequences:
        case_id = str(case.get("id") or "<missing-id>")
        sequence_ids.append(case_id)
        if case.get("schemaVersion") != 2:
            errors.append(f"{case_id}: sequence schemaVersion must be 2")
        if case.get("fixtureIsolation") != "ISOLATED_EVALUATION_ONLY":
            errors.append(f"{case_id}: fixtureIsolation must be ISOLATED_EVALUATION_ONLY")
        input_data = case.get("input")
        if not isinstance(input_data, dict):
            errors.append(f"{case_id}: input must be an object")
        else:
            for key in ("authToken", "expectedUserId"):
                if not str(input_data.get(key) or "").strip():
                    errors.append(f"{case_id}: input.{key} is required")
        steps = case.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append(f"{case_id}: steps must be a non-empty array")
            continue
        step_ids: list[str] = []
        for step in steps:
            if not isinstance(step, dict):
                errors.append(f"{case_id}: every step must be an object")
                continue
            step_id = str(step.get("id") or "")
            action = str(step.get("action") or "")
            step_ids.append(step_id)
            observed_actions.add(action)
            if not step_id:
                errors.append(f"{case_id}: every step requires an id")
            if action not in ALLOWED_ACTIONS:
                errors.append(f"{case_id}/{step_id}: unsupported action {action!r}")
            if not isinstance(step.get("params", {}), dict):
                errors.append(f"{case_id}/{step_id}: params must be an object")
            if not isinstance(step.get("expect", {}), dict):
                errors.append(f"{case_id}/{step_id}: expect must be an object")
        if len(step_ids) != len(set(step_ids)):
            errors.append(f"{case_id}: step IDs must be unique")
        expected = case.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{case_id}: expected must be an object")
        elif [str(value) for value in expected.get("requiredActions") or []] != [
            str(step.get("action") or "") for step in steps
        ]:
            errors.append(f"{case_id}: expected.requiredActions must bind every step in order")

    required_sequence_ids = [str(value) for value in lock.get("requiredSequenceIds") or []]
    if sequence_ids != required_sequence_ids:
        errors.append("sequence order/IDs differ from the frozen lock")
    required_actions = {str(value) for value in lock.get("requiredActions") or []}
    if required_actions != set(ALLOWED_ACTIONS):
        errors.append("lock requiredActions must list the complete v2 action surface")
    if observed_actions != required_actions:
        errors.append(
            "sequence dataset action coverage differs: "
            f"missing={sorted(required_actions - observed_actions)}, "
            f"unexpected={sorted(observed_actions - required_actions)}"
        )
    if lock.get("fixturePolicy") != "ISOLATED_EVALUATION_ONLY":
        errors.append("lock fixturePolicy must be ISOLATED_EVALUATION_ONLY")
    if not isinstance(lock.get("thresholds"), dict):
        errors.append("lock thresholds must be an object")
    if errors:
        raise EvaluationContractError("Agent v2 contract invalid:\n- " + "\n- ".join(errors))
    return {**lock, "datasetSha256": digest}


def load_bindings(path: Path | None) -> dict[str, str]:
    return v1.load_bindings(path)


def resolve_placeholders(value: Any, bindings: Mapping[str, str]) -> Any:
    try:
        return v1.resolve_placeholders(value, bindings)
    except v1.EvaluationContractError as exc:
        raise EvaluationContractError(str(exc)) from exc


def _lookup_one(root: Any, path: str) -> Any:
    current = root
    for token in [part for part in path.split(".") if part]:
        if isinstance(current, Mapping):
            if token not in current:
                raise EvaluationContractError(f"runtime state path does not exist: {path}")
            current = current[token]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise EvaluationContractError(
                    f"runtime state list path is invalid: {path}"
                ) from exc
        else:
            raise EvaluationContractError(f"runtime state path does not exist: {path}")
    return current


def resolve_state_references(value: Any, state: Mapping[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$state"}:
            path = str(value["$state"] or "").strip()
            if not path:
                raise EvaluationContractError("$state path cannot be blank")
            return _lookup_one(state, path)
        return {key: resolve_state_references(item, state) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_state_references(item, state) for item in value]
    return value


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, raw in value.items():
            item = raw
            if key in _JSON_FIELDS and isinstance(raw, str):
                try:
                    item = json.loads(raw)
                except json.JSONDecodeError:
                    item = raw
            normalized[str(key)] = _json_safe(item)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return "<bytes>"
    return value


def _values_at(root: Any, path: str) -> list[Any]:
    values = [root]
    for token in [part for part in path.split(".") if part]:
        next_values: list[Any] = []
        for current in values:
            if token == "*":
                if isinstance(current, Mapping):
                    next_values.extend(current.values())
                elif isinstance(current, Sequence) and not isinstance(
                    current, (str, bytes, bytearray)
                ):
                    next_values.extend(current)
                continue
            if isinstance(current, Mapping) and token in current:
                next_values.append(current[token])
                continue
            if isinstance(current, Sequence) and not isinstance(
                current, (str, bytes, bytearray)
            ):
                try:
                    next_values.append(current[int(token)])
                except (ValueError, IndexError):
                    pass
        values = next_values
    return values


def _recursive_subset(expected: Any, actual: Any) -> bool:
    return not v1.recursive_subset_mismatches(expected, actual)


def _collection_size(values: list[Any]) -> int:
    if len(values) == 1 and isinstance(values[0], (Mapping, list, tuple, set)):
        return len(values[0])
    return len(values)


def _check_passed(operation: str, values: list[Any], expected: Any) -> bool:
    first = values[0] if len(values) == 1 else None
    if operation == "exists":
        return bool(values) and any(value is not None for value in values)
    if operation == "notExists":
        return not values or all(value is None for value in values)
    if operation == "truthy":
        return bool(values) and all(bool(value) for value in values)
    if operation == "eq":
        return len(values) == 1 and first == expected
    if operation == "ne":
        return len(values) == 1 and first != expected
    if operation == "in":
        return len(values) == 1 and first in (expected or [])
    if operation == "contains":
        if len(values) == 1 and isinstance(first, (Mapping, list, tuple, set, str)):
            return expected in first
        return expected in values
    if operation == "notContains":
        return not _check_passed("contains", values, expected)
    if operation == "allEq":
        return bool(values) and all(value == expected for value in values)
    if operation == "allIn":
        allowed = expected or []
        return bool(values) and all(value in allowed for value in values)
    if operation == "subset":
        return len(values) == 1 and _recursive_subset(expected, first)
    if operation == "countEq":
        return _collection_size(values) == int(expected)
    if operation == "countGte":
        return _collection_size(values) >= int(expected)
    if operation == "countLte":
        return _collection_size(values) <= int(expected)
    if operation == "uniqueCountEq":
        return len({json.dumps(value, sort_keys=True, default=str) for value in values}) == int(
            expected
        )
    if operation == "gte":
        return len(values) == 1 and first is not None and first >= expected
    if operation == "lte":
        return len(values) == 1 and first is not None and first <= expected
    raise EvaluationContractError(f"unsupported state assertion operation: {operation}")


def evaluate_state_checks(
    checks: Sequence[Mapping[str, Any]], snapshot: Any
) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    for index, check in enumerate(checks):
        path = str(check.get("path") or "").strip()
        operation = str(check.get("op") or "eq")
        expected = check.get("value")
        values = _values_at(snapshot, path)
        passed = _check_passed(operation, values, expected)
        assertions.append(
            v1._assertion(
                str(check.get("name") or f"state:{index}:{path}:{operation}"),
                passed,
                expected=operation,
                actual={
                    "matchedPathValues": len(values),
                    "observedCollectionSize": _collection_size(values),
                },
                category=str(check.get("category") or "FINAL_STATE"),
            )
        )
    return assertions


def _envelope_assertions(
    expect: Mapping[str, Any], envelope: Mapping[str, Any]
) -> list[dict[str, Any]]:
    expected_code = int(expect.get("envelopeCode", 200))
    assertions = [
        v1._assertion(
            "business_envelope_code",
            envelope.get("code") == expected_code,
            expected=expected_code,
            actual=envelope.get("code"),
            category="EXECUTION" if expected_code == 200 else "SAFETY",
        )
    ]
    expected_info = expect.get("infoContains")
    if expected_info:
        info = str(envelope.get("info") or "")
        assertions.append(
            v1._assertion(
                "business_envelope_reason",
                str(expected_info) in info,
                expected="expected reason fragment",
                actual="matched" if str(expected_info) in info else "not matched",
                category="SAFETY",
            )
        )
    assertions.extend(evaluate_state_checks(expect.get("checks") or [], envelope.get("data")))
    return assertions


async def _request_envelope(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> dict[str, Any]:
    response = await client.request(method, url, **kwargs)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("service response is not a JSON object")
    return _json_safe(value)


class SequenceStateReader:
    """Read authoritative sequence state without accepting arbitrary SQL."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        agent_base_url: str,
        gateway_base_url: str,
    ) -> None:
        self.client = client
        self.agent_base_url = agent_base_url.rstrip("/")
        self.gateway_base_url = gateway_base_url.rstrip("/")

    @staticmethod
    async def database_now() -> str:
        """Use the database clock so fixture-window assertions avoid host timezone drift."""
        async with acquire() as cursor:
            await cursor.execute("SELECT NOW(6) AS evaluation_started_at")
            row = await cursor.fetchone()
        if not row or row.get("evaluation_started_at") is None:
            raise RuntimeError("cannot read the authoritative evaluation database clock")
        return str(_json_safe(row["evaluation_started_at"]))

    @staticmethod
    async def _query_rows(
        table: str,
        columns: str,
        query: Mapping[str, Any],
        allowed_filters: Mapping[str, str],
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        for public_name, column in allowed_filters.items():
            value = query.get(public_name)
            if value in (None, "", []):
                continue
            if isinstance(value, list):
                placeholders = ",".join(["%s"] * len(value))
                where.append(f"{column} IN ({placeholders})")
                params.extend(value)
            else:
                where.append(f"{column}=%s")
                params.append(value)
        if query.get("createdAfter"):
            where.append("created_at >= %s")
            params.append(query["createdAfter"])
        clause = " AND ".join(where) if where else "1=1"
        limit = max(1, min(int(query.get("limit") or 100), 500))
        sql = f"SELECT {columns} FROM {table} WHERE {clause} ORDER BY created_at ASC LIMIT %s"
        params.append(limit)
        async with acquire() as cursor:
            await cursor.execute(sql, tuple(params))
            rows = await cursor.fetchall()
        return [_json_safe(row) for row in rows]

    async def read(
        self,
        source: str,
        query: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> Any:
        user_id = str(state["case"]["userId"])
        token = str(state["case"]["authToken"])
        if source == "runtime":
            return state
        if source == "mission":
            return _json_safe(await shopping_mission_service.load(user_id))
        if source == "profile":
            return _json_safe(await shopping_profile_service.get_profile(user_id))
        if source == "pendingAction":
            action_token = str(query.get("actionToken") or state.get("actionToken") or "")
            if not action_token:
                raise EvaluationContractError("pendingAction requires a runtime action token")
            return _json_safe(await pending_action_store.get(action_token))
        if source == "episode":
            run_id = str(query.get("runId") or (state.get("lastEpisode") or {}).get("runId") or "")
            if not run_id:
                return state.get("lastEpisode")
            return _json_safe(await episode_query_service.detail(run_id))
        if source == "ledger":
            rows = await self._query_rows(
                "commerce_outcome_ledger",
                (
                    "event_id,source,idempotency_key,event_type,user_id,request_id,run_id,"
                    "product_id,sku_key,order_id,payload_json,occurred_at,created_at"
                ),
                {**query, "userId": query.get("userId") or user_id},
                {
                    "userId": "user_id",
                    "eventTypes": "event_type",
                    "productIds": "product_id",
                    "requestId": "request_id",
                    "orderId": "order_id",
                    "idempotencyKey": "idempotency_key",
                },
            )
            counts = Counter(str(row.get("event_type") or "") for row in rows)
            return {"rows": rows, "count": len(rows), "countsByEventType": dict(counts)}
        if source == "recommendationEvents":
            rows = await self._query_rows(
                "agent_recommendation_event",
                (
                    "event_id,user_id,request_id,product_id,position,source,retrieval_mode,"
                    "match_type,subject_label,recall_source,model_version,run_id,event_type,"
                    "occurred_at,created_at"
                ),
                {**query, "userId": query.get("userId") or user_id},
                {
                    "userId": "user_id",
                    "eventTypes": "event_type",
                    "productIds": "product_id",
                    "requestId": "request_id",
                },
            )
            counts = Counter(str(row.get("event_type") or "") for row in rows)
            return {"rows": rows, "count": len(rows), "countsByEventType": dict(counts)}
        if source == "offerSnapshots":
            rows = await self._query_rows(
                "agent_final_offer_snapshot",
                "snapshot_id,user_id,product_id,sku_key,offer_json,expires_at,created_at",
                {**query, "userId": query.get("userId") or user_id},
                {
                    "userId": "user_id",
                    "productIds": "product_id",
                    "snapshotIds": "snapshot_id",
                },
            )
            return {"rows": rows, "count": len(rows)}
        if source == "visualSelection":
            rows = await self._query_rows(
                "agent_visual_selection",
                (
                    "selection_id,user_id,image_asset_id,subjects_json,status,"
                    "selected_subject_id,selected_message_id,created_at,updated_at"
                ),
                {**query, "userId": query.get("userId") or user_id},
                {
                    "userId": "user_id",
                    "selectionId": "selection_id",
                    "status": "status",
                },
            )
            return {"rows": rows, "count": len(rows)}
        if source == "javaCart":
            return (
                await _request_envelope(
                    self.client,
                    "POST",
                    f"{self.gateway_base_url}/api/productCart/loadProductCart",
                    data={"pageNo": "1"},
                    headers={"token": token},
                )
            ).get("data")
        if source == "javaOrder":
            pay_order_id = query.get("payOrderId")
            order_id = query.get("orderId")
            order: Any = None
            if pay_order_id:
                order = (
                    await _request_envelope(
                        self.client,
                        "POST",
                        f"{self.gateway_base_url}/api/order/getOrderInfo",
                        data={"payOrderId": pay_order_id},
                        headers={"token": token},
                    )
                ).get("data")
                order_id = (order or {}).get("orderId") or order_id
            detail: Any = None
            if order_id:
                detail = (
                    await _request_envelope(
                        self.client,
                        "POST",
                        f"{self.gateway_base_url}/api/order/getMyOrderDetail",
                        data={"orderId": order_id},
                        headers={"token": token},
                    )
                ).get("data")
            return {"order": order, "detail": detail}
        if source == "supportCases":
            return (
                await _request_envelope(
                    self.client,
                    "GET",
                    f"{self.agent_base_url}/api/agent/supportCases",
                    params={"limit": int(query.get("limit") or 20)},
                    headers={"token": token},
                )
            ).get("data")
        raise EvaluationContractError(f"unsupported authoritative state source: {source}")


class SequenceEvaluator:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        agent_base_url: str,
        gateway_base_url: str,
        internal_token: str | None,
        timeout_seconds: float,
        expected_configured_mode: str,
        state_reader: SequenceStateReader | None = None,
    ) -> None:
        self.client = client
        self.agent_base_url = agent_base_url.rstrip("/")
        self.gateway_base_url = gateway_base_url.rstrip("/")
        self.internal_token = str(internal_token or "").strip()
        self.timeout_seconds = timeout_seconds
        self.expected_configured_mode = expected_configured_mode
        self.state_reader = state_reader or SequenceStateReader(
            client=client,
            agent_base_url=agent_base_url,
            gateway_base_url=gateway_base_url,
        )
        self._last_agent_call_at = 0.0

    async def _respect_agent_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_agent_call_at
        if elapsed < 1.05:
            await asyncio.sleep(1.05 - elapsed)
        self._last_agent_call_at = time.monotonic()

    async def _agent_run(
        self,
        *,
        endpoint: str,
        form: Mapping[str, Any],
        token: str,
        expected_user_id: str,
        expect: Mapping[str, Any],
    ) -> dict[str, Any]:
        await self._respect_agent_rate_limit()
        envelope = await _request_envelope(
            self.client,
            "POST",
            f"{self.agent_base_url}/api/agent/{endpoint}",
            data={key: value for key, value in form.items() if value is not None},
            headers={"token": token},
        )
        assertions = _envelope_assertions(expect, envelope)
        if envelope.get("code") != 200:
            return {"data": envelope.get("data"), "assertions": assertions}
        submitted = envelope.get("data")
        if not isinstance(submitted, dict):
            raise RuntimeError(f"{endpoint} did not return an object")
        if str(submitted.get("userId") or "") != expected_user_id:
            raise RuntimeError(f"{endpoint} resolved to a different fixture user")
        run_id = str(submitted.get("runId") or "")
        message_id = int(submitted.get("messageId") or 0)
        if not run_id or not message_id:
            raise RuntimeError(f"{endpoint} did not return runId/messageId")
        episode = await v1._poll_episode(run_id, self.timeout_seconds)
        message = _json_safe(await agent_message_service.admin_get_message(message_id) or {})
        action_token = v1._action_token(message)
        pending = await pending_action_store.get(action_token) if action_token else None
        episode_expected = expect.get("episode") or {}
        episode_result = v1.evaluate_episode(
            {"expected": episode_expected},
            episode,
            pending=pending,
            expected_configured_mode=self.expected_configured_mode,
        )
        assertions.extend(episode_result["assertions"])
        assertions.extend(evaluate_state_checks(expect.get("messageChecks") or [], message))
        return {
            "data": _json_safe(submitted),
            "message": message,
            "episode": _json_safe(episode),
            "pending": _json_safe(pending),
            "actionToken": action_token,
            "assertions": assertions,
            "provider": episode_result["provider"],
            "metrics": episode_result["metrics"],
            "tools": episode_result["tools"],
            "events": episode_result["events"],
        }

    async def _simple_http_action(
        self,
        *,
        method: str,
        url: str,
        token: str | None,
        expect: Mapping[str, Any],
        data: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = dict(headers or {})
        if token:
            request_headers["token"] = token
        envelope = await _request_envelope(
            self.client,
            method,
            url,
            data=data,
            json=json_body,
            headers=request_headers,
        )
        return {
            "data": envelope.get("data"),
            "assertions": _envelope_assertions(expect, envelope),
        }

    async def _dispatch(
        self,
        action: str,
        params: Mapping[str, Any],
        expect: Mapping[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        token = str(state["case"]["authToken"])
        expected_user_id = str(state["case"]["userId"])
        if action == "sendMessage":
            form = {
                "message": str(params.get("message") or ""),
                "fromProduct": str(params.get("fromProduct", False)).lower(),
                "consultProductId": params.get("consultProductId"),
                "comparisonProductIds": (
                    json.dumps(params.get("comparisonProductIds"), ensure_ascii=False)
                    if isinstance(params.get("comparisonProductIds"), list)
                    else params.get("comparisonProductIds")
                ),
                "imageAssetId": params.get("imageAssetId"),
            }
            return await self._agent_run(
                endpoint="sendMessage",
                form=form,
                token=token,
                expected_user_id=expected_user_id,
                expect=expect,
            )
        if action == "selectVisualSubject":
            return await self._agent_run(
                endpoint="selectVisualSubject",
                form={
                    "selectionId": params.get("selectionId"),
                    "subjectId": params.get("subjectId"),
                },
                token=token,
                expected_user_id=expected_user_id,
                expect=expect,
            )
        if action == "reportClick":
            return await self._simple_http_action(
                method="POST",
                url=f"{self.agent_base_url}/api/agent/reportClick",
                token=token,
                expect=expect,
                data={
                    "productId": params.get("productId"),
                    "requestId": params.get("requestId"),
                    "position": params.get("position"),
                },
            )
        if action == "addToCart":
            return await self._simple_http_action(
                method="POST",
                url=f"{self.gateway_base_url}/api/productCart/add2Cart",
                token=token,
                expect=expect,
                data=params,
            )
        if action == "createOrder":
            body = dict(params)
            idempotency_key = str(body.pop("idempotencyKey", "")).strip()
            if not idempotency_key:
                raise EvaluationContractError("createOrder requires idempotencyKey")
            return await self._simple_http_action(
                method="POST",
                url=f"{self.gateway_base_url}/api/order/postOrder",
                token=token,
                expect=expect,
                json_body=body,
                headers={"Idempotency-Key": idempotency_key},
            )
        if action == "markPaymentSuccess":
            if not self.internal_token:
                raise EvaluationContractError(
                    "markPaymentSuccess requires private AISHOP_INTERNAL_TOKEN"
                )
            return await self._simple_http_action(
                method="POST",
                url=f"{self.gateway_base_url}/internal/order/paySuccess",
                token=None,
                expect=expect,
                json_body={
                    "payOrderId": params.get("payOrderId"),
                    "channelOrderId": params.get("channelOrderId"),
                },
                headers={"X-Internal-Token": self.internal_token},
            )
        if action == "requestRefund":
            idempotency_key = str(params.get("idempotencyKey") or "").strip()
            if not idempotency_key:
                raise EvaluationContractError("requestRefund requires idempotencyKey")
            return await self._simple_http_action(
                method="POST",
                url=f"{self.gateway_base_url}/api/order/refundOrder",
                token=token,
                expect=expect,
                data={"orderItemId": params.get("orderItemId")},
                headers={"Idempotency-Key": idempotency_key},
            )
        if action == "postReview":
            idempotency_key = str(params.get("idempotencyKey") or "").strip()
            if not idempotency_key:
                raise EvaluationContractError("postReview requires idempotencyKey")
            return await self._simple_http_action(
                method="POST",
                url=f"{self.gateway_base_url}/api/order/comment/postComment",
                token=token,
                expect=expect,
                data={
                    "orderId": params.get("orderId"),
                    "commentContent": params.get("commentContent"),
                    "commentImages": params.get("commentImages", ""),
                    "star": params.get("star"),
                },
                headers={"Idempotency-Key": idempotency_key},
            )
        if action == "confirmAgentAction":
            action_token = str(params.get("actionToken") or state.get("actionToken") or "")
            if not action_token:
                raise EvaluationContractError(
                    "confirmAgentAction requires a durable runtime action token"
                )
            await self._respect_agent_rate_limit()
            envelope = await _request_envelope(
                self.client,
                "POST",
                f"{self.agent_base_url}/api/agent/confirmAction",
                data={"actionToken": action_token},
                headers={"token": token},
            )
            assertions = _envelope_assertions(expect, envelope)
            statuses = {str(value) for value in expect.get("pendingStatuses") or []}
            if envelope.get("code") == 200:
                pending = await v1._poll_pending(
                    action_token,
                    statuses or {"CONFIRMED", "FAILED", "INCONCLUSIVE", "MANUAL_REVIEW"},
                    self.timeout_seconds,
                )
                assertions.extend(evaluate_state_checks(expect.get("pendingChecks") or [], pending))
            else:
                pending = None
            return {
                "data": envelope.get("data"),
                "pending": _json_safe(pending),
                "actionToken": action_token,
                "assertions": assertions,
            }
        if action == "assertState":
            source = str(params.get("source") or "")
            query = params.get("query") or {}
            snapshot: Any = None
            deadline = asyncio.get_running_loop().time() + float(
                params.get("pollSeconds") or 0
            )
            while True:
                snapshot = await self.state_reader.read(source, query, state)
                assertions = evaluate_state_checks(expect.get("checks") or [], snapshot)
                if all(item["passed"] for item in assertions):
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    break
                await asyncio.sleep(0.25)
            return {"data": None, "assertions": assertions, "snapshotObserved": True}
        raise EvaluationContractError(f"unsupported sequence action: {action}")

    async def execute(self, case: Mapping[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        input_data = case["input"]
        state: dict[str, Any] = {
            "case": {
                "id": case["id"],
                "authToken": input_data["authToken"],
                "userId": input_data["expectedUserId"],
                "startedAt": datetime.now(timezone.utc).isoformat(),
            },
            "steps": {},
            "episodes": [],
        }
        assertions: list[dict[str, Any]] = []
        actions: list[str] = []
        tools: list[str] = []
        events: list[str] = []
        providers: list[Mapping[str, Any]] = []
        metrics: list[Mapping[str, Any]] = []
        step_summaries: list[dict[str, Any]] = []
        error: dict[str, str] | None = None

        try:
            state["case"]["startedAt"] = await self.state_reader.database_now()
            for raw_step in case["steps"]:
                step = resolve_state_references(raw_step, state)
                step_id = str(step["id"])
                action = str(step["action"])
                actions.append(action)
                result = await self._dispatch(
                    action,
                    step.get("params") or {},
                    step.get("expect") or {},
                    state,
                )
                step_assertions = list(result.get("assertions") or [])
                assertions.extend(step_assertions)
                tools.extend(str(value) for value in result.get("tools") or [])
                events.extend(str(value) for value in result.get("events") or [])
                if isinstance(result.get("provider"), Mapping):
                    providers.append(result["provider"])
                if isinstance(result.get("metrics"), Mapping):
                    metrics.append(result["metrics"])
                runtime_result = {
                    key: value
                    for key, value in result.items()
                    if key
                    in {
                        "data",
                        "message",
                        "episode",
                        "pending",
                        "actionToken",
                    }
                }
                state["steps"][step_id] = {"action": action, **runtime_result}
                if result.get("message") is not None:
                    state["lastMessage"] = result["message"]
                if result.get("episode") is not None:
                    state["lastEpisode"] = result["episode"]
                    state["episodes"].append(result["episode"])
                if result.get("pending") is not None:
                    state["lastPending"] = result["pending"]
                if result.get("data") is not None:
                    state["lastData"] = result["data"]
                if result.get("actionToken"):
                    state["actionToken"] = result["actionToken"]
                step_summaries.append(
                    {
                        "stepId": step_id,
                        "action": action,
                        "passed": all(item.get("passed") is True for item in step_assertions),
                    }
                )
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)[:300]}

        expected_actions = [str(value) for value in case["expected"]["requiredActions"]]
        assertions.append(
            v1._assertion(
                "sequence_action_order",
                v1._ordered_subsequence(expected_actions, actions),
                expected=expected_actions,
                actual=actions,
                category="TOOL_SELECTION",
            )
        )
        max_actions = case["expected"].get("maxActions")
        if max_actions is not None:
            assertions.append(
                v1._assertion(
                    "sequence_action_budget",
                    len(actions) <= int(max_actions),
                    expected=f"<={max_actions}",
                    actual=len(actions),
                    category="BUDGET",
                )
            )
        provider_complete = bool(providers) and all(
            item.get("complete") is True for item in providers
        )
        if case["expected"].get("providerComplete") is True:
            assertions.append(
                v1._assertion(
                    "sequence_provider_complete",
                    provider_complete,
                    expected=True,
                    actual=provider_complete,
                    category="PROVIDER",
                )
            )

        input_tokens = sum(int(item.get("inputTokens") or 0) for item in metrics)
        output_tokens = sum(int(item.get("outputTokens") or 0) for item in metrics)
        cost = sum(float(item.get("costCny") or 0) for item in metrics)
        model_names = sorted(
            {
                str(model)
                for provider in providers
                for model in provider.get("modelNames") or []
            }
        )
        rag_complete = all(
            (provider.get("rag") or {}).get("complete") is True
            for provider in providers
            if (provider.get("rag") or {}).get("retrievalEventCount", 0)
        )
        task_success = error is None and all(item.get("passed") is True for item in assertions)
        return {
            "caseId": case["id"],
            "subset": case["subset"],
            "executionMode": EXECUTION_MODE,
            "runId": None,
            "taskSuccess": task_success,
            "assertions": assertions,
            "steps": step_summaries,
            "tools": tools,
            "events": events,
            "orchestration": {
                "mode": "sequence",
                "configuredMode": self.expected_configured_mode,
            },
            "provider": {
                "llmCallCount": sum(int(item.get("llmCallCount") or 0) for item in providers),
                "modelNames": model_names,
                "failedLlmCalls": sum(
                    int(item.get("failedLlmCalls") or 0) for item in providers
                ),
                "complete": provider_complete,
                "rag": {"complete": rag_complete},
            },
            "metrics": {
                "traceSteps": sum(int(item.get("traceSteps") or 0) for item in metrics),
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "costCny": round(cost, 6),
                "latencyMs": round((time.monotonic() - started) * 1000, 3),
                "ttftMs": next(
                    (item.get("ttftMs") for item in metrics if item.get("ttftMs") is not None),
                    None,
                ),
            },
            "error": error,
        }


def aggregate(
    results: list[dict[str, Any]], preflight_facts: Mapping[str, Any]
) -> dict[str, Any]:
    summary = v1.aggregate(results, preflight_facts)
    summary["toolArgumentAccuracy"] = summary["toolParameterAccuracy"]
    summary["terminalStateAccuracy"] = summary["finalStateAccuracy"]
    summary["severeSafetyViolationCount"] = summary["criticalSafetyViolationCount"]
    return summary


def threshold_failures(
    summary: Mapping[str, Any], thresholds: Mapping[str, Any]
) -> list[str]:
    comparisons = (
        ("taskSuccessRate", "taskSuccessRateMin"),
        ("executionCompletenessRate", "executionCompletenessRateMin"),
        ("providerCompletenessRate", "providerCompletenessRateMin"),
        ("toolSelectionAccuracy", "toolSelectionAccuracyMin"),
        ("toolArgumentAccuracy", "toolArgumentAccuracyMin"),
        ("terminalStateAccuracy", "terminalStateAccuracyMin"),
    )
    failures: list[str] = []
    for metric, threshold_key in comparisons:
        minimum = float(thresholds.get(threshold_key, 0))
        actual = float(summary.get(metric) or 0)
        if actual < minimum:
            failures.append(f"{metric}={actual:.4f} < {minimum:.4f}")
    maximum = int(thresholds.get("severeSafetyViolationCountMax", 0))
    actual_safety = int(summary.get("severeSafetyViolationCount") or 0)
    if actual_safety > maximum:
        failures.append(f"severeSafetyViolationCount={actual_safety} > {maximum}")
    return failures


def _safe_case_result(result: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "caseId",
        "subset",
        "executionMode",
        "runId",
        "taskSuccess",
        "assertions",
        "steps",
        "tools",
        "events",
        "orchestration",
        "provider",
        "metrics",
        "error",
    }
    return {key: value for key, value in result.items() if key in allowed}


def write_report(report: Mapping[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = report["summary"]
    failed = [case for case in report["cases"] if not case.get("taskSuccess")]
    lines = [
        "# AI_Shop Agent v2 live task-success evaluation",
        "",
        f"- Run: `{report['metadata']['runId']}`",
        f"- Dataset SHA-256: `{report['metadata']['datasetSha256']}`",
        f"- Fixture snapshot: `{report['metadata']['fixtureSnapshotId']}`",
        f"- Cases: {summary['passedCount']}/{summary['caseCount']}",
        f"- Task success rate: {summary['taskSuccessRate']:.2%}",
        f"- Execution completeness: {summary['executionCompletenessRate']:.2%}",
        f"- Provider completeness: {summary['providerCompletenessRate']:.2%}",
        f"- Tool selection accuracy: {summary['toolSelectionAccuracy']:.2%}",
        f"- Tool argument accuracy: {summary['toolArgumentAccuracy']:.2%}",
        f"- Terminal state accuracy: {summary['terminalStateAccuracy']:.2%}",
        f"- Severe safety violations: {summary['severeSafetyViolationCount']}",
        f"- Gate: {'PASS' if not report['gateFailures'] else 'FAIL'}",
        "",
        "## Failed cases",
        "",
    ]
    if failed:
        for case in failed:
            reason = case.get("error") or [
                item["name"] for item in case.get("assertions") or [] if not item["passed"]
            ]
            lines.append(f"- `{case['caseId']}`: {reason}")
    else:
        lines.append("- None")
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _require_fixture_flags(
    cases: Sequence[Mapping[str, Any]], bindings: Mapping[str, str]
) -> None:
    missing: list[str] = []
    for case in cases:
        if case.get("kind") != "sequence":
            continue
        for name in case.get("requiredFixtureFlags") or []:
            if str(bindings.get(str(name)) or "").strip().lower() != "enabled":
                missing.append(f"{case.get('id')}:{name}")
    if missing:
        raise EvaluationContractError(
            "sequence fixtures are disabled; restore the isolated snapshot and set flags: "
            + ", ".join(sorted(missing))
        )


def _short_git_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{7,40}", value):
        raise EvaluationContractError("cannot resolve a valid git commit for the formal run ID")
    return value


def _default_run_id(mode: str) -> str:
    now = datetime.now(timezone.utc)
    mode_label = mode.replace("_", "-")
    return f"agent-v2-{mode_label}-{_short_git_sha()}-{now:%Y%m%d}-{now:%H%M%S}"


async def run_live(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    cases = load_cases(args.dataset)
    contract = validate_contract(cases, args.dataset, args.lock)
    bindings = load_bindings(args.bindings)
    if args.subset:
        selected_subsets = set(args.subset)
        cases = [case for case in cases if case.get("subset") in selected_subsets]
        if not cases:
            raise EvaluationContractError("subset selection produced no cases")
    if args.case_id:
        selected_ids = set(args.case_id)
        cases = [case for case in cases if str(case.get("id")) in selected_ids]
        if not cases:
            raise EvaluationContractError("case selection produced no cases")
    _require_fixture_flags(cases, bindings)
    resolved = [resolve_placeholders(case, bindings) for case in cases]
    if not str(args.fixture_snapshot_id or "").strip():
        raise EvaluationContractError("--fixture-snapshot-id is required for every live v2 run")
    if any(
        step.get("action") == "markPaymentSuccess"
        for case in resolved
        for step in case.get("steps") or []
    ) and not str(args.internal_token or "").strip():
        raise EvaluationContractError(
            "AISHOP_INTERNAL_TOKEN is required when payment-success steps are selected"
        )

    run_id = args.run_id or _default_run_id(args.expected_orchestration_mode)
    if not re.fullmatch(str(contract["runIdPattern"]), run_id):
        raise EvaluationContractError(f"run ID does not match Agent v2 contract: {run_id}")

    timeout = httpx.Timeout(connect=5, read=max(10.0, args.timeout), write=15, pool=5)
    async with httpx.AsyncClient(timeout=timeout) as client:
        facts = await v1.preflight(client, args.api_base_url.rstrip("/"))
        await init_pool()
        try:
            single = v1.LiveTaskEvaluator(
                client=client,
                api_base_url=args.api_base_url,
                timeout_seconds=args.timeout,
                expected_configured_mode=args.expected_orchestration_mode,
            )
            sequence = SequenceEvaluator(
                client=client,
                agent_base_url=args.api_base_url,
                gateway_base_url=args.gateway_base_url,
                internal_token=args.internal_token,
                timeout_seconds=args.timeout,
                expected_configured_mode=args.expected_orchestration_mode,
            )
            results: list[dict[str, Any]] = []
            for index, case in enumerate(resolved, 1):
                print(f"[{index}/{len(resolved)}] {case['id']}", flush=True)
                if case.get("kind", "single_turn") == "single_turn":
                    results.append(await single.execute(case))
                else:
                    results.append(await sequence.execute(case))
        finally:
            await close_pool()

    summary = aggregate(results, facts)
    gate_failures = threshold_failures(summary, contract["thresholds"])
    report = {
        "schemaVersion": "aishop-live-eval/v1",
        "metadata": {
            "suite": SUITE,
            "runId": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "executionMode": EXECUTION_MODE,
            "evidenceSource": "SYNTHETIC+local-live",
            "simulated": False,
            "dataset": str(args.dataset),
            "datasetSha256": contract["datasetSha256"],
            "knownDatasetSha256": contract["knownDatasetSha256"],
            "caseCount": len(resolved),
            "configuredOrchestrationMode": args.expected_orchestration_mode,
            "fixtureSnapshotId": args.fixture_snapshot_id,
            "apiBaseUrl": args.api_base_url,
            "gatewayBaseUrl": args.gateway_base_url,
            "fixturePolicy": contract["fixturePolicy"],
        },
        "providerPreflight": facts,
        "summary": summary,
        "gateFailures": gate_failures,
        "cases": [_safe_case_result(result) for result in results],
    }
    output_dir = RESULTS_ROOT / run_id
    write_report(report, output_dir)
    return report, output_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument(
        "--bindings",
        type=Path,
        default=(
            Path(os.environ["AISHOP_EVAL_BINDINGS_FILE"])
            if os.environ.get("AISHOP_EVAL_BINDINGS_FILE")
            else None
        ),
        help="Untracked JSON object that binds ${NAME} fixture placeholders",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("AISHOP_EVAL_API_BASE_URL", "http://127.0.0.1:7050"),
    )
    parser.add_argument(
        "--gateway-base-url",
        default=os.environ.get("AISHOP_EVAL_GATEWAY_BASE_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument(
        "--internal-token",
        default=os.environ.get("AISHOP_INTERNAL_TOKEN"),
        help="Private internal token; never persisted in the report",
    )
    parser.add_argument("--run-id")
    parser.add_argument("--fixture-snapshot-id")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--subset", action="append")
    parser.add_argument(
        "--case-id",
        action="append",
        help="Run only the selected frozen case ID; repeat to select multiple cases",
    )
    parser.add_argument(
        "--expected-orchestration-mode",
        choices=("adaptive", "workflow", "single_agent", "multi_agent"),
        default=os.environ.get("AISHOP_ORCHESTRATION_MODE", "adaptive"),
    )
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        cases = load_cases(args.dataset)
        contract = validate_contract(cases, args.dataset, args.lock)
        if args.validate_only:
            print(json.dumps({"valid": True, "contract": contract}, indent=2))
            return
        report, output_dir = asyncio.run(run_live(args))
    except (EvaluationContractError, OSError, httpx.HTTPError, subprocess.SubprocessError) as exc:
        print(f"Agent v2 evaluation aborted: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(
        json.dumps(
            {
                "resultDir": str(output_dir),
                "summary": report["summary"],
                "gateFailures": report["gateFailures"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if report["gateFailures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
