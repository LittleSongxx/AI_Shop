from __future__ import annotations

import json
from urllib.parse import quote

from app.config.settings import get_settings
from app.db.pool import acquire


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
    ) -> dict:
        page_no = max(1, int(page_no))
        page_size = min(100, max(1, int(page_size)))
        clauses = ["1=1"]
        params: list[object] = []
        for column, value in (
            ("status", status),
            ("intent", intent),
            ("user_id", user_id),
            ("outcome", outcome),
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
                       outcome,scenario,intent,queue_name,model_name,input_tokens,
                       output_tokens,cost_cny,latency_ms,capture_level,
                       dataset_eligible,started_at,completed_at
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
                       error_code,error_message,latency_ms,occurred_at
                FROM agent_step
                WHERE run_id=%s
                ORDER BY occurred_at,step_id
                """,
                (run_id,),
            )
            steps = list(await cur.fetchall())
        return {
            **self._public_run(run),
            "version": self._decode(run.get("version_json")),
            "experiment": self._decode(run.get("experiment_json")),
            "quality": self._decode(run.get("quality_json")),
            "rewardSignals": self._decode(run.get("reward_signals_json")),
            "steps": [self._public_step(row) for row in steps],
        }

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
            "modelName": row.get("model_name"),
            "inputTokens": int(row.get("input_tokens") or 0),
            "outputTokens": int(row.get("output_tokens") or 0),
            "costCny": float(row.get("cost_cny") or 0),
            "latencyMs": row.get("latency_ms"),
            "captureLevel": row.get("capture_level"),
            "datasetEligible": row.get("dataset_eligible"),
            "startedAt": EpisodeQueryService._format_time(row.get("started_at")),
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
