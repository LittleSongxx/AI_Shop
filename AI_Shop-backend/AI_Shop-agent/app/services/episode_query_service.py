from __future__ import annotations

import json
from urllib.parse import quote

from app.config.settings import get_settings
from app.db.pool import acquire
from app.services.episode_evaluator import evaluate_order_aftersales_episode


class EpisodeQueryService:
    async def list_runs(
        self,
        *,
        page_no: int = 1,
        page_size: int = 30,
        status: str | None = None,
        intent: str | None = None,
        user_id: str | None = None,
        outcome: str | None = None,
        agent_id: str | None = None,
        run_scope: str = "ROOT",
    ) -> dict:
        page_no = max(1, int(page_no))
        page_size = min(100, max(1, int(page_size)))
        clauses = ["1=1"]
        params: list[object] = []
        normalized_scope = str(run_scope or "ROOT").strip().upper()
        if normalized_scope == "ROOT":
            clauses.append("parent_run_id IS NULL")
        elif normalized_scope == "CHILD":
            clauses.append("parent_run_id IS NOT NULL")
        elif normalized_scope != "ALL":
            normalized_scope = "ROOT"
            clauses.append("parent_run_id IS NULL")
        for column, value in (
            ("status", status),
            ("intent", intent),
            ("user_id", user_id),
            ("outcome", outcome),
            ("agent_id", agent_id),
        ):
            cleaned = str(value or "").strip()
            if cleaned:
                clauses.append(f"{column}=%s")
                params.append(cleaned)
        where = " AND ".join(clauses)
        async with acquire() as cur:
            await cur.execute(f"SELECT COUNT(*) AS cnt FROM agent_run WHERE {where}", params)
            total = int((await cur.fetchone() or {}).get("cnt") or 0)
            await cur.execute(
                f"""
                SELECT run_id,message_id,user_id,session_id,otel_trace_id,status,
                       COALESCE(agent_id, 'supervisor') AS agent_id,
                       COALESCE(agent_version, 'v1') AS agent_version,
                       parent_run_id,handoff_id,COALESCE(actor_type, 'USER') AS actor_type,
                       outcome,scenario,intent,queue_name,model_name,input_tokens,
                       output_tokens,cost_cny,latency_ms,capture_level,
                       dataset_eligible,dataset_reviewed_by,dataset_reviewed_at,
                       dataset_review_note,started_at,completed_at,
                       (SELECT user_message FROM agent_message
                        WHERE message_id=agent_run.message_id) AS user_message
                FROM agent_run
                WHERE {where}
                ORDER BY started_at DESC, run_id DESC
                LIMIT %s OFFSET %s
                """,
                [*params, page_size, (page_no - 1) * page_size],
            )
            rows = list(await cur.fetchall())
        return {
            "list": [self._public_run(row) for row in rows],
            "totalCount": total,
            "pageNo": page_no,
            "pageSize": page_size,
            "runScope": normalized_scope,
        }

    async def detail(self, run_id: str) -> dict | None:
        run_id = str(run_id or "").strip()
        if not run_id:
            return None
        async with acquire() as cur:
            await cur.execute("SELECT * FROM agent_run WHERE run_id=%s", (run_id,))
            run = await cur.fetchone()
            if not run:
                return None
            await cur.execute(
                """
                SELECT step_id,event_type,node_name,round_no,status,span_id,
                       input_json,output_json,model_name,tool_name,call_id,
                       error_code,error_message,latency_ms,occurred_at,
                       agent_id,artifact_type,handoff_id
                FROM agent_step
                WHERE run_id=%s
                ORDER BY occurred_at,step_id
                """,
                (run_id,),
            )
            steps = list(await cur.fetchall())
            await cur.execute(
                """
                SELECT handoff_id,parent_run_id,child_run_id,source_agent,target_agent,status,
                       input_summary_json,artifact_summary_json,latency_ms,error_code,completed_at,created_at
                FROM agent_handoff WHERE parent_run_id=%s OR child_run_id=%s
                ORDER BY created_at
                """,
                (run_id, run_id),
            )
            handoffs = list(await cur.fetchall())
            parent_run_id = run.get("parent_run_id")
            message_id = run.get("message_id")
            if not message_id and parent_run_id:
                await cur.execute(
                    "SELECT message_id FROM agent_run WHERE run_id=%s",
                    (parent_run_id,),
                )
                parent = await cur.fetchone() or {}
                message_id = parent.get("message_id")
            conversation = None
            if message_id:
                await cur.execute(
                    """
                    SELECT message_id,user_message,assistant_message,biz_type,source_refs
                    FROM agent_message WHERE message_id=%s
                    """,
                    (message_id,),
                )
                message = await cur.fetchone()
                if message:
                    conversation = {
                        "messageId": message.get("message_id"),
                        "userMessage": message.get("user_message") or "",
                        "assistantMessage": message.get("assistant_message") or "",
                        "bizType": message.get("biz_type"),
                        "sourceRefs": self._decode(message.get("source_refs")) or [],
                    }
            children: list[dict] = []
            if not parent_run_id:
                await cur.execute(
                    "SELECT * FROM agent_run WHERE parent_run_id=%s ORDER BY started_at,run_id",
                    (run_id,),
                )
                children = [self._public_run(item) for item in await cur.fetchall()]
        public = {
            **self._public_run(run),
            "version": self._decode(run.get("version_json")),
            "experiment": self._decode(run.get("experiment_json")),
            "quality": self._decode(run.get("quality_json")),
            "rewardSignals": self._decode(run.get("reward_signals_json")),
            "steps": [self._public_step(row) for row in steps],
            "handoffs": [self._public_handoff(row) for row in handoffs],
            "children": children,
            "conversation": conversation,
        }
        public["episodeEvaluation"] = evaluate_order_aftersales_episode(public)
        return public

    @staticmethod
    def _public_run(row: dict) -> dict:
        trace_id = row.get("otel_trace_id")
        return {
            "runId": row.get("run_id"),
            "messageId": row.get("message_id"),
            "userId": row.get("user_id"),
            "sessionId": row.get("session_id"),
            "traceId": trace_id,
            "tempoTraceUrl": EpisodeQueryService._tempo_url(trace_id),
            "status": row.get("status"),
            "outcome": row.get("outcome"),
            "scenario": row.get("scenario"),
            "intent": row.get("intent"),
            "queueName": row.get("queue_name"),
            "agentId": row.get("agent_id") or "supervisor",
            "agentVersion": row.get("agent_version") or "v1",
            "parentRunId": row.get("parent_run_id"),
            "handoffId": row.get("handoff_id"),
            "actorType": row.get("actor_type") or "USER",
            "modelName": row.get("model_name"),
            "inputTokens": int(row.get("input_tokens") or 0),
            "outputTokens": int(row.get("output_tokens") or 0),
            "costCny": float(row.get("cost_cny") or 0),
            "latencyMs": row.get("latency_ms"),
            "captureLevel": row.get("capture_level"),
            "datasetEligible": row.get("dataset_eligible"),
            "datasetReviewedBy": row.get("dataset_reviewed_by"),
            "datasetReviewedAt": EpisodeQueryService._format_time(
                row.get("dataset_reviewed_at")
            ),
            "datasetReviewNote": row.get("dataset_review_note"),
            "startedAt": EpisodeQueryService._format_time(row.get("started_at")),
            "completedAt": EpisodeQueryService._format_time(row.get("completed_at")),
            "userMessage": row.get("user_message") or "",
        }

    @staticmethod
    def _public_handoff(row: dict) -> dict:
        return {
            "handoffId": row.get("handoff_id"),
            "parentRunId": row.get("parent_run_id"),
            "childRunId": row.get("child_run_id"),
            "sourceAgent": row.get("source_agent"),
            "targetAgent": row.get("target_agent"),
            "status": row.get("status"),
            "inputSummary": EpisodeQueryService._decode(row.get("input_summary_json")),
            "artifactSummary": EpisodeQueryService._decode(row.get("artifact_summary_json")),
            "latencyMs": row.get("latency_ms"),
            "errorCode": row.get("error_code"),
            "completedAt": EpisodeQueryService._format_time(row.get("completed_at")),
        }

    @staticmethod
    def _public_step(row: dict) -> dict:
        return {
            "stepId": row.get("step_id"),
            "eventType": row.get("event_type"),
            "nodeName": row.get("node_name"),
            "roundNo": row.get("round_no"),
            "status": row.get("status"),
            "spanId": row.get("span_id"),
            "input": EpisodeQueryService._decode(row.get("input_json")),
            "output": EpisodeQueryService._decode(row.get("output_json")),
            "modelName": row.get("model_name"),
            "toolName": row.get("tool_name"),
            "callId": row.get("call_id"),
            "errorCode": row.get("error_code"),
            "errorMessage": row.get("error_message"),
            "latencyMs": row.get("latency_ms"),
            "occurredAt": EpisodeQueryService._format_time(row.get("occurred_at")),
            "agentId": row.get("agent_id"),
            "artifactType": row.get("artifact_type"),
            "handoffId": row.get("handoff_id"),
        }

    @staticmethod
    def _decode(value):
        if value is None or isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None

    @staticmethod
    def _format_time(value) -> str | None:
        return value.isoformat(timespec="milliseconds") if value else None

    @staticmethod
    def _tempo_url(trace_id: str | None) -> str | None:
        if not trace_id:
            return None
        base = get_settings().tempo_query_url.strip()
        if not base:
            return None
        separator = "&" if "?" in base else "?"
        return f"{base}{separator}traceId={quote(str(trace_id))}"


episode_query_service = EpisodeQueryService()
