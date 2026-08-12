"""Evidence metrics with explicit denominators, windows, and honest empty states."""

from __future__ import annotations

import csv
import io
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.pool import acquire
from app.services.pilot_batch_service import participant_user_hash

_EVIDENCE_SOURCES = frozenset({"SYNTHETIC", "LOCAL_PILOT", "REAL_USER"})
_TERMINAL_STATUSES = frozenset(
    {"SUCCEEDED", "FAILED", "CANCELLED", "HANDOFF", "DEGRADED"}
)


def _decode_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _verified_success(run: dict[str, Any]) -> bool:
    """Only deterministic verification or explicit success confirmation counts."""
    quality = _decode_object(run.get("quality_json"))
    signals = _decode_object(run.get("reward_signals_json"))
    verifier = _decode_object(signals.get("verifier"))
    return bool(
        quality.get("verifierPassed") is True
        or verifier.get("passed") is True
        or signals.get("userConfirmedSuccess") is True
        or signals.get("humanResolved") is True
    )


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": int(numerator),
        "denominator": int(denominator),
        "rate": round(numerator / denominator, 6) if denominator else None,
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(ordered[lower], 3)
    interpolated = ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)
    return round(interpolated, 3)


def _distribution(values: list[float]) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None and float(value) >= 0]
    return {
        "sampleSize": len(clean),
        "p50": _percentile(clean, 0.50),
        "p95": _percentile(clean, 0.95),
        # Reporting a P99 from a handful of local runs is false precision.
        "p99": _percentile(clean, 0.99) if len(clean) >= 100 else None,
        "p99Status": "measured" if len(clean) >= 100 else "样本少于 100，未报告",
    }


def _event_time(row: dict[str, Any]) -> datetime | None:
    value = row.get("occurred_at")
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    return None


def _recommendation_funnel(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_touchpoint: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    impressions: list[dict[str, Any]] = []
    for event in events:
        request_id = str(event.get("request_id") or "")
        user_id = str(event.get("user_id") or "")
        product_id = str(event.get("product_id") or "")
        if not request_id or not user_id or not product_id:
            continue
        key = (request_id, user_id, product_id)
        by_touchpoint[key].append(event)
        if str(event.get("event_type") or "").upper() == "IMPRESSION":
            impressions.append(event)

    clicked = added = paid = negative = 0
    for impression in impressions:
        started = _event_time(impression)
        if started is None:
            continue
        key = (
            str(impression.get("request_id") or ""),
            str(impression.get("user_id") or ""),
            str(impression.get("product_id") or ""),
        )
        subsequent = [
            (str(event.get("event_type") or "").upper(), _event_time(event))
            for event in by_touchpoint[key]
        ]
        clicked += int(
            any(kind == "CLICK" and at and started <= at <= started + timedelta(hours=24)
                for kind, at in subsequent)
        )
        added += int(
            any(kind == "ADD_TO_CART" and at and started <= at <= started + timedelta(hours=24)
                for kind, at in subsequent)
        )
        paid += int(
            any(kind in {"PAYMENT", "REPEAT_PURCHASE"} and at
                and started <= at <= started + timedelta(days=7)
                for kind, at in subsequent)
        )
        negative += int(
            any(kind in {"CANCEL", "REFUND", "RETURN", "SUPPORT_CONTACT"} and at
                and started <= at <= started + timedelta(days=7)
                for kind, at in subsequent)
        )
    total = len(impressions)
    return {
        "unit": "verified recommendation touchpoint",
        "clickWithin24h": _ratio(clicked, total),
        "addToCartWithin24h": _ratio(added, total),
        "paymentWithin7d": _ratio(paid, total),
        "negativeOutcomeWithin7d": _ratio(negative, total),
    }


def summarize_pilot_metrics(
    runs: list[dict[str, Any]], events: list[dict[str, Any]]
) -> dict[str, Any]:
    terminal = [
        run for run in runs if str(run.get("status") or "").upper() in _TERMINAL_STATUSES
    ]
    successes = [run for run in terminal if _verified_success(run)]
    support_events_by_user: dict[str, list[datetime]] = defaultdict(list)
    for event in events:
        if str(event.get("event_type") or "").upper() != "SUPPORT_CONTACT":
            continue
        occurred_at = _event_time(event)
        if occurred_at:
            support_events_by_user[str(event.get("user_id") or "")].append(occurred_at)
    fcr_successes = []
    for run in successes:
        completed_at = run.get("completed_at") or run.get("started_at")
        if isinstance(completed_at, datetime):
            completed_at = (
                completed_at.replace(tzinfo=timezone.utc)
                if completed_at.tzinfo is None
                else completed_at
            )
        if str(run.get("status") or "").upper() == "HANDOFF" or not isinstance(
            completed_at, datetime
        ):
            continue
        reopened = any(
            completed_at <= occurred_at <= completed_at + timedelta(hours=24)
            for occurred_at in support_events_by_user.get(str(run.get("user_id") or ""), [])
        )
        if not reopened:
            fcr_successes.append(run)
    source_counts = Counter(str(run.get("evidence_source") or "") for run in runs)
    return {
        "tasks": {
            "executed": len(runs),
            "terminal": len(terminal),
            "verifiedSuccess": _ratio(len(successes), len(terminal)),
            "verifiedSuccessDefinition": (
                "deterministic verifier passed, explicit user success confirmation, "
                "or completed human resolution"
            ),
            "fcr24h": _ratio(len(fcr_successes), len(successes)),
            "fcrDefinition": (
                "verified success with no handoff or support contact within the 24h evidence window"
            ),
        },
        "funnel": _recommendation_funnel(events),
        "sampleSources": dict(sorted(source_counts.items())),
        "realUserStatus": "已采集" if source_counts.get("REAL_USER") else "未采集",
    }


def summarize_performance(runs: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [run for run in runs if _verified_success(run)]
    total_cost = round(sum(float(run.get("cost_cny") or 0) for run in runs), 6)
    return {
        "runCount": len(runs),
        "latencyMs": _distribution(
            [run["latency_ms"] for run in runs if run.get("latency_ms") is not None]
        ),
        "ttftMs": _distribution(
            [run["ttft_ms"] for run in runs if run.get("ttft_ms") is not None]
        ),
        "steps": _distribution(
            [run["step_count"] for run in runs if run.get("step_count") is not None]
        ),
        "modelCalls": _distribution(
            [run["model_calls"] for run in runs if run.get("model_calls") is not None]
        ),
        "toolCalls": _distribution(
            [run["tool_calls"] for run in runs if run.get("tool_calls") is not None]
        ),
        "tokens": {
            "input": int(sum(int(run.get("input_tokens") or 0) for run in runs)),
            "output": int(sum(int(run.get("output_tokens") or 0) for run in runs)),
        },
        "costCny": total_cost,
        "verifiedSuccessCount": len(successes),
        "costPerVerifiedSuccessCny": (
            round(total_cost / len(successes), 6) if successes else None
        ),
    }


class PilotMetricsService:
    @staticmethod
    def _filters(
        *,
        batch_id: str | None,
        evidence_source: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
        alias: str = "r",
    ) -> tuple[list[str], list[Any]]:
        clauses = [f"{alias}.parent_run_id IS NULL", f"{alias}.evidence_source IS NOT NULL"]
        params: list[Any] = []
        if batch_id:
            clauses.append(f"{alias}.pilot_batch_id=%s")
            params.append(str(batch_id))
        if evidence_source:
            source = str(evidence_source).upper()
            if source not in _EVIDENCE_SOURCES:
                raise ValueError("evidenceSource 无效")
            clauses.append(f"{alias}.evidence_source=%s")
            params.append(source)
        if start_at:
            clauses.append(f"{alias}.started_at>=%s")
            params.append(start_at)
        if end_at:
            clauses.append(f"{alias}.started_at<%s")
            params.append(end_at)
        return clauses, params

    async def _active_participants(
        self, batch_id: str | None
    ) -> dict[str, str] | None:
        if not batch_id:
            return None
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT user_id_hash,pseudonym
                FROM agent_pilot_participant
                WHERE batch_id=%s AND status='ACTIVE'
                """,
                (str(batch_id),),
            )
            rows = list(await cur.fetchall())
        return {str(row["user_id_hash"]): str(row["pseudonym"]) for row in rows}

    async def _load_runs(
        self,
        *,
        batch_id: str | None,
        evidence_source: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
        clauses, params = self._filters(
            batch_id=batch_id,
            evidence_source=evidence_source,
            start_at=start_at,
            end_at=end_at,
        )
        async with acquire() as cur:
            await cur.execute(
                f"""
                SELECT r.run_id,r.user_id,r.status,r.outcome,r.intent,
                       r.latency_ms,r.ttft_ms,r.input_tokens,r.output_tokens,
                       r.cost_cny,r.quality_json,r.reward_signals_json,
                       r.started_at,r.completed_at,r.evidence_source,r.pilot_batch_id,
                       (SELECT COUNT(*) FROM agent_step s WHERE s.run_id=r.run_id)
                           AS step_count,
                       (SELECT COUNT(*) FROM agent_step s
                         WHERE s.run_id=r.run_id AND s.event_type='LLM_CALL')
                           AS model_calls,
                       (SELECT COUNT(*) FROM agent_step s
                         WHERE s.run_id=r.run_id AND s.tool_name IS NOT NULL)
                           AS tool_calls
                FROM agent_run r
                WHERE {' AND '.join(clauses)}
                ORDER BY r.started_at
                LIMIT 10000
                """,
                tuple(params),
            )
            runs = list(await cur.fetchall())
        participants = await self._active_participants(batch_id)
        if participants is not None:
            runs = [run for run in runs if participant_user_hash(run["user_id"]) in participants]
        return runs, participants

    async def _load_events(
        self,
        *,
        batch_id: str | None,
        evidence_source: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
        participants: dict[str, str] | None,
    ) -> list[dict[str, Any]]:
        clauses = ["r.parent_run_id IS NULL", "r.evidence_source IS NOT NULL"]
        params: list[Any] = []
        if batch_id:
            clauses.append("r.pilot_batch_id=%s")
            params.append(str(batch_id))
        if evidence_source:
            clauses.append("r.evidence_source=%s")
            params.append(str(evidence_source).upper())
        if start_at:
            clauses.append("l.occurred_at>=%s")
            params.append(start_at)
        if end_at:
            clauses.append("l.occurred_at<%s")
            params.append(end_at + timedelta(days=7))
        async with acquire() as cur:
            await cur.execute(
                f"""
                SELECT l.event_id,l.event_type,l.user_id,l.request_id,l.product_id,
                       l.order_id,l.occurred_at,l.run_id
                FROM commerce_outcome_ledger l
                INNER JOIN agent_run r ON r.run_id=l.run_id
                WHERE {' AND '.join(clauses)}
                ORDER BY l.occurred_at
                LIMIT 50000
                """,
                tuple(params),
            )
            events = list(await cur.fetchall())
        if participants is not None:
            events = [
                event
                for event in events
                if participant_user_hash(event["user_id"]) in participants
            ]
        return events

    async def overview(
        self,
        *,
        batch_id: str | None = None,
        evidence_source: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> dict[str, Any]:
        runs, participants = await self._load_runs(
            batch_id=batch_id,
            evidence_source=evidence_source,
            start_at=start_at,
            end_at=end_at,
        )
        events = await self._load_events(
            batch_id=batch_id,
            evidence_source=evidence_source,
            start_at=start_at,
            end_at=end_at,
            participants=participants,
        )
        return {
            "schemaVersion": "aishop-pilot-metrics/v1",
            "filters": {
                "batchId": batch_id,
                "evidenceSource": evidence_source,
                "startAt": start_at,
                "endAt": end_at,
            },
            "sampleDisclosure": {
                "runLimit": 10000,
                "outcomeLimit": 50000,
                "rawConversationExported": False,
                "withdrawnParticipantsExcluded": batch_id is not None,
            },
            **summarize_pilot_metrics(runs, events),
        }

    async def performance(self, **filters: Any) -> dict[str, Any]:
        runs, _participants = await self._load_runs(**filters)
        return {
            "schemaVersion": "aishop-pilot-performance/v1",
            "filters": filters,
            **summarize_performance(runs),
        }

    async def report(self, batch_id: str) -> dict[str, Any]:
        runs, participants = await self._load_runs(
            batch_id=batch_id,
            evidence_source=None,
            start_at=None,
            end_at=None,
        )
        events = await self._load_events(
            batch_id=batch_id,
            evidence_source=None,
            start_at=None,
            end_at=None,
            participants=participants,
        )
        overview = summarize_pilot_metrics(runs, events)
        per_alias: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for run in runs:
            alias = (participants or {}).get(participant_user_hash(run["user_id"]))
            if alias:
                per_alias[alias].append(run)
        participant_metrics = []
        suppressed = 0
        for alias, alias_runs in sorted(per_alias.items()):
            if len(alias_runs) < 5:
                suppressed += 1
                continue
            participant_metrics.append(
                {
                    "pseudonym": alias,
                    "runCount": len(alias_runs),
                    "verifiedSuccess": _ratio(
                        sum(_verified_success(run) for run in alias_runs), len(alias_runs)
                    ),
                }
            )
        return {
            "schemaVersion": "aishop-pilot-report/v1",
            "batchId": batch_id,
            "privacy": {
                "rawConversationExported": False,
                "rawUserIdExported": False,
                "minimumGroupSize": 5,
                "suppressedParticipantGroups": suppressed,
                "withdrawnParticipantsExcluded": True,
            },
            **overview,
            "performance": summarize_performance(runs),
            "participantMetrics": participant_metrics,
        }

    async def export_report(self, batch_id: str, output_format: str) -> tuple[bytes, str]:
        report = await self.report(batch_id)
        fmt = str(output_format or "json").strip().lower()
        if fmt == "json":
            return (
                json.dumps(report, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
                "application/json",
            )
        if fmt == "markdown":
            tasks = report["tasks"]
            performance = report["performance"]
            lines = [
                f"# Pilot batch {batch_id}",
                "",
                f"- Runs: {tasks['executed']}",
                f"- Verified success: {tasks['verifiedSuccess']['numerator']} / "
                f"{tasks['verifiedSuccess']['denominator']}",
                f"- FCR (24h): {tasks['fcr24h']['numerator']} / "
                f"{tasks['fcr24h']['denominator']}",
                f"- Cost (CNY): {performance['costCny']}",
                f"- REAL_USER: {report['realUserStatus']}",
                "- Raw conversation exported: no",
                "- Raw user ID exported: no",
                f"- Suppressed participant groups (<5): "
                f"{report['privacy']['suppressedParticipantGroups']}",
            ]
            return "\n".join(lines).encode("utf-8"), "text/markdown; charset=utf-8"
        if fmt == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["metric", "numerator", "denominator", "rate", "window"])
            for name, metric, window in (
                ("verified_success", report["tasks"]["verifiedSuccess"], "task"),
                ("fcr", report["tasks"]["fcr24h"], "24h"),
                ("click", report["funnel"]["clickWithin24h"], "24h"),
                ("add_to_cart", report["funnel"]["addToCartWithin24h"], "24h"),
                ("payment", report["funnel"]["paymentWithin7d"], "7d"),
                ("negative", report["funnel"]["negativeOutcomeWithin7d"], "7d"),
            ):
                writer.writerow(
                    [name, metric["numerator"], metric["denominator"], metric["rate"], window]
                )
            return output.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8"
        raise ValueError("format 必须是 json、csv 或 markdown")


pilot_metrics_service = PilotMetricsService()
