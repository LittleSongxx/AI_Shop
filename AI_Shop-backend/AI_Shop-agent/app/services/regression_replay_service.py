from __future__ import annotations

import json
from typing import Any

from app.db.pool import acquire
from app.domain.intent.classifier import resolve_intent
from app.services.badcase_service import badcase_service
from app.services.episode_evaluator import (
    evaluate_order_aftersales_episode,
    recursive_subset_match,
)
from app.services.episode_query_service import episode_query_service


class RegressionReplayService:
    async def run_active(self, case_id: int | None = None) -> dict[str, Any]:
        cases = await self._load_active(case_id)
        results: list[dict[str, Any]] = []
        for case in cases:
            result = await self.run_case(case)
            await badcase_service.record_regression_result(int(case["case_id"]), result["result"])
            results.append(result)
        return {
            "total": len(results),
            "passed": sum(item["result"] == "PASS" for item in results),
            "failed": sum(item["result"] == "FAIL" for item in results),
            "errors": sum(item["result"] == "ERROR" for item in results),
            "results": results,
        }

    async def run_case(self, case: dict[str, Any]) -> dict[str, Any]:
        case_id = int(case.get("case_id") or case.get("caseId") or 0)
        input_data = _object(case.get("input_json") or case.get("input"))
        expected = _object(case.get("expected_json") or case.get("expected"))
        try:
            actual, replay_type = await self._execute(input_data)
            mismatches = recursive_subset_match(expected, actual)
            return {
                "caseId": case_id,
                "name": case.get("name"),
                "replayType": replay_type,
                "result": "FAIL" if mismatches else "PASS",
                "mismatches": mismatches,
                "actual": actual,
            }
        except Exception as exc:
            return {
                "caseId": case_id,
                "name": case.get("name"),
                "replayType": "UNKNOWN",
                "result": "ERROR",
                "mismatches": [f"replay raised {type(exc).__name__}"],
                "actual": {},
            }

    async def _execute(self, input_data: dict[str, Any]) -> tuple[dict[str, Any], str]:
        episode = input_data.get("episode")
        if isinstance(episode, dict):
            return evaluate_order_aftersales_episode(episode), "EPISODE"

        run_id = str(input_data.get("runId") or "").strip()
        if run_id:
            detail = await episode_query_service.detail(run_id)
            if detail is None:
                raise ValueError("Episode no longer exists")
            return evaluate_order_aftersales_episode(detail), "EPISODE"

        user_text = str(
            input_data.get("userMessage")
            or input_data.get("message")
            or input_data.get("text")
            or ""
        ).strip()
        if not user_text:
            raise ValueError("Text regression case has no message")
        decision = await resolve_intent(
            str(input_data.get("userId") or "regression-replay"),
            user_text,
            from_product=bool(input_data.get("fromProduct")),
            allow_llm=False,
            session_intent=str(input_data.get("sessionIntent") or "").strip() or None,
            record_metrics=False,
            after_sales_workflow=True,
        )
        actual = decision.model_dump(mode="json")
        actual.update(
            {
                "intentType": actual["intent"],
                "nextAction": actual.pop("next_action"),
                "riskLevel": actual.pop("risk_level"),
            }
        )
        return actual, "TEXT_INTENT"

    @staticmethod
    async def _load_active(case_id: int | None) -> list[dict[str, Any]]:
        clauses = ["status='ACTIVE'"]
        params: list[object] = []
        if case_id is not None:
            clauses.append("case_id=%s")
            params.append(int(case_id))
        async with acquire() as cur:
            await cur.execute(
                f"""
                SELECT * FROM agent_regression_case
                WHERE {" AND ".join(clauses)}
                ORDER BY case_id
                """,
                params,
            )
            rows = list(await cur.fetchall())
        if case_id is not None and not rows:
            raise ValueError("ACTIVE 回归 Case 不存在")
        return rows


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


regression_replay_service = RegressionReplayService()
