from __future__ import annotations

import json
from typing import Any

from app.db.pool import transaction
from app.harness.metrics.runtime_sensors import DATASET_REVIEW_TOTAL
from app.services.episode_evaluator import evaluate_order_aftersales_episode
from app.services.episode_query_service import episode_query_service
from app.services.episode_service import text_fingerprint

DATASET_REVIEW_STATUSES = frozenset({"UNREVIEWED", "APPROVED", "REJECTED"})
_PENDING_STATUS_NAMES = {
    0: "PENDING",
    1: "CONFIRMED",
    2: "CANCELLED",
    3: "EXECUTING",
    4: "FAILED",
    5: "EXPIRED",
    6: "INCONCLUSIVE",
    7: "MANUAL_REVIEW",
}


class EpisodeReviewService:
    async def review(
        self,
        run_id: str,
        decision: str,
        reviewer: str,
        *,
        note: str | None = None,
    ) -> dict[str, Any]:
        run_id = str(run_id or "").strip()
        decision = str(decision or "").strip().upper()
        reviewer = str(reviewer or "").strip()
        if not run_id:
            raise ValueError("runId 不能为空")
        if decision not in DATASET_REVIEW_STATUSES:
            raise ValueError("datasetEligible 只支持 UNREVIEWED/APPROVED/REJECTED")
        if not reviewer:
            raise ValueError("reviewer 不能为空")
        clean_note = str(note or "").strip()[:1000] or None

        async with transaction() as cur:
            await cur.execute("SELECT * FROM agent_run WHERE run_id=%s FOR UPDATE", (run_id,))
            run = await cur.fetchone()
            if not run:
                raise ValueError("Episode 不存在或已过保留期")

            quality = _object(run.get("quality_json"))
            reward_signals = _object(run.get("reward_signals_json"))
            reward_signals.update(await self._load_domain_facts(cur, run_id))
            episode = {
                **run,
                "quality": quality,
                "rewardSignals": reward_signals,
                "datasetEligible": decision,
            }
            evaluation = evaluate_order_aftersales_episode(episode)
            if decision == "APPROVED" and not evaluation["reviewEligible"]:
                raise ValueError(f"Episode 不满足训练数据批准条件：{evaluation['verdict']}")

            if decision == "UNREVIEWED":
                await cur.execute(
                    """
                    UPDATE agent_run
                    SET dataset_eligible='UNREVIEWED', dataset_reviewed_by=NULL,
                        dataset_reviewed_at=NULL, dataset_review_note=NULL,
                        reward_signals_json=JSON_MERGE_PATCH(
                            COALESCE(reward_signals_json,JSON_OBJECT()), %s)
                    WHERE run_id=%s
                    """,
                    (_json(reward_signals), run_id),
                )
            else:
                await cur.execute(
                    """
                    UPDATE agent_run
                    SET dataset_eligible=%s, dataset_reviewed_by=%s,
                        dataset_reviewed_at=NOW(3), dataset_review_note=%s,
                        reward_signals_json=JSON_MERGE_PATCH(
                            COALESCE(reward_signals_json,JSON_OBJECT()), %s)
                    WHERE run_id=%s
                    """,
                    (
                        decision,
                        reviewer[:100],
                        clean_note,
                        _json(reward_signals),
                        run_id,
                    ),
                )

        DATASET_REVIEW_TOTAL.labels(decision=decision).inc()
        detail = await episode_query_service.detail(run_id)
        if detail is None:
            raise RuntimeError("Episode 审核成功但详情读取失败")
        return detail

    @staticmethod
    async def _load_domain_facts(cur, run_id: str) -> dict[str, Any]:
        facts: dict[str, Any] = {}
        await cur.execute(
            """
            SELECT action_type,status,params_json
            FROM agent_pending_action
            WHERE run_id=%s
            ORDER BY created_at DESC LIMIT 1
            """,
            (run_id,),
        )
        pending = await cur.fetchone()
        if pending:
            raw_status = pending.get("status")
            if isinstance(raw_status, int) or str(raw_status or "").isdigit():
                status_name = _PENDING_STATUS_NAMES.get(int(raw_status or 0), "UNKNOWN")
            else:
                status_name = str(raw_status or "UNKNOWN").upper()
            params = _object(pending.get("params_json"))
            action_type = str(pending.get("action_type") or "").upper()
            facts.update(
                {
                    "actionType": action_type,
                    "actionProposed": True,
                    "userConfirmed": status_name
                    in {"CONFIRMED", "EXECUTING", "FAILED", "INCONCLUSIVE", "MANUAL_REVIEW"},
                    "remoteOutcomeKnown": status_name in {"CONFIRMED", "FAILED"},
                    "outcome": status_name,
                }
            )
            if action_type == "CANCEL_ORDER":
                facts["orderStatusBefore"] = params.get("orderStatusBefore")
                facts["orderStatusAfter"] = 4 if status_name == "CONFIRMED" else None

        await cur.execute(
            """
            SELECT status,category,resolution_code,root_cause,resolution_summary,
                   support_session_id
            FROM support_case
            WHERE run_id=%s
            ORDER BY created_at DESC LIMIT 1
            """,
            (run_id,),
        )
        support_case = await cur.fetchone()
        if support_case:
            case_status = str(support_case.get("status") or "").upper()
            facts.update(
                {
                    "actionType": facts.get("actionType") or "CREATE_SUPPORT_CASE",
                    "caseCreated": True,
                    "caseCategory": support_case.get("category"),
                    "caseStatus": case_status,
                    "supportSessionId": support_case.get("support_session_id"),
                }
            )
            if case_status == "RESOLVED":
                summary = str(support_case.get("resolution_summary") or "")
                facts.update(
                    {
                        "humanResolved": True,
                        "humanResolutionCode": support_case.get("resolution_code"),
                        "humanRootCause": support_case.get("root_cause"),
                        "humanResolutionSummaryFingerprint": text_fingerprint(summary),
                    }
                )
        return facts


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


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


episode_review_service = EpisodeReviewService()
