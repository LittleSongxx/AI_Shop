from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from app.db.pool import acquire
from app.services.episode_service import sanitize_episode_payload

BADCASE_STATUSES = frozenset(
    {
        "NEW",
        "TRIAGED",
        "LABELED",
        "FIXING",
        "REGRESSION_ADDED",
        "VERIFIED",
        "CLOSED",
        "IGNORED",
        "NOT_A_BUG",
    }
)
_TERMINAL_STATUSES = frozenset({"CLOSED", "IGNORED", "NOT_A_BUG"})
_TRANSITIONS = {
    "NEW": {"TRIAGED", "IGNORED", "NOT_A_BUG"},
    "TRIAGED": {"LABELED", "IGNORED", "NOT_A_BUG"},
    "LABELED": {"FIXING", "REGRESSION_ADDED", "IGNORED", "NOT_A_BUG"},
    "FIXING": {"REGRESSION_ADDED", "IGNORED", "NOT_A_BUG"},
    "REGRESSION_ADDED": {"VERIFIED", "IGNORED", "NOT_A_BUG"},
    "VERIFIED": {"CLOSED"},
}
_SOURCES = frozenset(
    {"USER_FEEDBACK", "USER_CORRECTION", "VERIFIER", "JUDGE", "TOOL", "SYSTEM"}
)
_SEVERITIES = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})
_CORRECTION_RE = re.compile(
    r"(?:^|[，。！？\s])(不对|错了|不是这样|你理解错了|答非所问|并没有|纠正一下)"
)


class BadcaseService:
    async def add_candidate(
        self,
        message_id: int | None,
        candidate_type: str,
        reason: str,
        *,
        run_id: str | None = None,
        source: str = "SYSTEM",
        severity: str = "MEDIUM",
        snapshot: dict | None = None,
        judge: dict | None = None,
    ) -> int:
        candidate_type = str(candidate_type or "UNKNOWN")[:32].upper()
        source = str(source or "SYSTEM").upper()
        severity = str(severity or "MEDIUM").upper()
        if source not in _SOURCES:
            source = "SYSTEM"
        if severity not in _SEVERITIES:
            severity = "MEDIUM"
        snapshot_json = _json(sanitize_episode_payload(snapshot or {}))
        judge_json = _json(sanitize_episode_payload(judge)) if judge else None

        async with acquire() as cur:
            if run_id is None and message_id is not None:
                await cur.execute(
                    "SELECT run_id FROM agent_message WHERE message_id=%s",
                    (message_id,),
                )
                message = await cur.fetchone()
                run_id = str((message or {}).get("run_id") or "") or None
            await cur.execute(
                """
                INSERT IGNORE INTO ai_badcase_candidate
                    (message_id, run_id, candidate_type, reason, status, source,
                     severity, snapshot_json, judge_json, occurrence_count,
                     first_seen_at, created_at, updated_at)
                VALUES (%s,%s,%s,%s,'NEW',%s,%s,%s,%s,1,NOW(),NOW(),NOW())
                """,
                (
                    message_id,
                    run_id,
                    candidate_type,
                    str(reason or "未提供原因")[:255],
                    source,
                    severity,
                    snapshot_json,
                    judge_json,
                ),
            )
            if cur.rowcount == 0 and message_id is not None:
                await cur.execute(
                    """
                    UPDATE ai_badcase_candidate
                    SET reason=%s, run_id=COALESCE(run_id,%s), source=%s,
                        severity=%s, snapshot_json=%s,
                        judge_json=COALESCE(%s,judge_json),
                        occurrence_count=occurrence_count+1, updated_at=NOW()
                    WHERE message_id=%s AND candidate_type=%s
                    """,
                    (
                        str(reason or "未提供原因")[:255],
                        run_id,
                        source,
                        severity,
                        snapshot_json,
                        judge_json,
                        message_id,
                        candidate_type,
                    ),
                )
            await cur.execute(
                """
                SELECT candidate_id FROM ai_badcase_candidate
                WHERE message_id <=> %s AND candidate_type=%s
                ORDER BY candidate_id DESC LIMIT 1
                """,
                (message_id, candidate_type),
            )
            row = await cur.fetchone()
        if not row:
            raise RuntimeError("badcase candidate was not persisted")
        return int(row["candidate_id"])

    async def detect_user_correction(
        self,
        *,
        user_id: str,
        current_message_id: int,
        user_text: str,
    ) -> int | None:
        match = _CORRECTION_RE.search(str(user_text or ""))
        if not match:
            return None
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT message_id,run_id FROM agent_message
                WHERE user_id=%s AND message_id<%s
                  AND assistant_message IS NOT NULL
                ORDER BY message_id DESC LIMIT 1
                """,
                (user_id, current_message_id),
            )
            previous = await cur.fetchone()
        if not previous:
            return None
        return await self.add_candidate(
            int(previous["message_id"]),
            "USER_CORRECTION",
            f"用户后续明确纠正：{match.group(1)}",
            run_id=previous.get("run_id"),
            source="USER_CORRECTION",
            severity="HIGH",
            snapshot={
                "correctionMessageId": current_message_id,
                "signal": match.group(1),
            },
        )

    async def list_candidates(
        self,
        page_no: int = 1,
        page_size: int = 30,
        status: str | None = "NEW",
    ) -> dict:
        page_no = max(1, int(page_no))
        page_size = max(1, min(int(page_size), 100))
        clauses = ["1=1"]
        params: list[object] = []
        normalized_status = str(status or "").strip().upper()
        if normalized_status:
            if normalized_status not in BADCASE_STATUSES:
                raise ValueError("未知 Badcase 状态")
            clauses.append("b.status=%s")
            params.append(normalized_status)
        where = " AND ".join(clauses)
        async with acquire() as cur:
            await cur.execute(
                f"SELECT COUNT(*) AS cnt FROM ai_badcase_candidate b WHERE {where}",
                params,
            )
            total = int((await cur.fetchone() or {}).get("cnt") or 0)
            await cur.execute(
                f"""
                SELECT b.*,m.user_message,m.assistant_message,m.intent,
                       m.intent_confidence,m.sentiment,r.case_key,r.last_result
                FROM ai_badcase_candidate b
                LEFT JOIN agent_message m ON m.message_id=b.message_id
                LEFT JOIN agent_regression_case r ON r.case_id=b.regression_case_id
                WHERE {where}
                ORDER BY b.updated_at DESC,b.candidate_id DESC
                LIMIT %s OFFSET %s
                """,
                [*params, page_size, (page_no - 1) * page_size],
            )
            rows = list(await cur.fetchall())
        return {
            "totalCount": total,
            "pageNo": page_no,
            "pageSize": page_size,
            "pageTotal": (total + page_size - 1) // page_size if total else 0,
            "list": [_public_candidate(row) for row in rows],
        }

    async def review(
        self,
        candidate_id: int,
        next_status: str,
        reviewer: str,
        *,
        remark: str | None = None,
        labels: list[str] | None = None,
        owner: str | None = None,
        fix_version: str | None = None,
        regression: dict | None = None,
    ) -> dict:
        next_status = str(next_status or "").strip().upper()
        if next_status not in BADCASE_STATUSES - {"NEW"}:
            raise ValueError("未知 Badcase 目标状态")
        reviewer = str(reviewer or "").strip()
        if not reviewer:
            raise ValueError("reviewer 不能为空")

        async with acquire() as cur:
            await cur.execute(
                """
                SELECT b.*,m.user_message,m.assistant_message,m.intent,
                       r.last_result
                FROM ai_badcase_candidate b
                LEFT JOIN agent_message m ON m.message_id=b.message_id
                LEFT JOIN agent_regression_case r ON r.case_id=b.regression_case_id
                WHERE b.candidate_id=%s FOR UPDATE
                """,
                (candidate_id,),
            )
            row = await cur.fetchone()
            if not row:
                raise ValueError("坏例不存在")
            current = str(row.get("status") or "NEW").upper()
            if current in _TERMINAL_STATUSES:
                raise ValueError("坏例已经进入终态")
            if next_status != current and next_status not in _TRANSITIONS.get(current, set()):
                raise ValueError(f"Badcase 状态不能从 {current} 变更为 {next_status}")
            clean_labels = sorted(
                {str(label).strip()[:64] for label in labels or [] if str(label).strip()}
            )
            if next_status == "LABELED" and not clean_labels:
                raise ValueError("进入 LABELED 前至少需要一个标签")
            if next_status == "FIXING" and not str(owner or row.get("owner") or "").strip():
                raise ValueError("进入 FIXING 前必须指定 owner")

            regression_case_id = row.get("regression_case_id")
            if next_status == "REGRESSION_ADDED" and not regression_case_id:
                regression_case_id = await self._create_regression_case(
                    cur, row, regression or {}, reviewer
                )
            if next_status == "VERIFIED":
                if not regression_case_id:
                    raise ValueError("VERIFIED 需要关联回归 Case")
                if str(row.get("last_result") or "").upper() != "PASS":
                    raise ValueError("回归 Case 最近一次结果为 PASS 后才能 VERIFIED")

            await cur.execute(
                """
                UPDATE ai_badcase_candidate
                SET status=%s,reviewer=%s,review_remark=%s,
                    labels_json=COALESCE(%s,labels_json),
                    owner=COALESCE(%s,owner),fix_version=COALESCE(%s,fix_version),
                    regression_case_id=COALESCE(%s,regression_case_id),updated_at=NOW()
                WHERE candidate_id=%s
                """,
                (
                    next_status,
                    reviewer[:100],
                    str(remark or "")[:500] or None,
                    _json(clean_labels) if clean_labels else None,
                    str(owner or "").strip()[:100] or None,
                    str(fix_version or "").strip()[:64] or None,
                    regression_case_id,
                    candidate_id,
                ),
            )
        return await self.get(candidate_id)

    async def get(self, candidate_id: int) -> dict:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT b.*,m.user_message,m.assistant_message,m.intent,
                       m.intent_confidence,m.sentiment,r.case_key,r.last_result
                FROM ai_badcase_candidate b
                LEFT JOIN agent_message m ON m.message_id=b.message_id
                LEFT JOIN agent_regression_case r ON r.case_id=b.regression_case_id
                WHERE b.candidate_id=%s
                """,
                (candidate_id,),
            )
            row = await cur.fetchone()
        if not row:
            raise ValueError("坏例不存在")
        return _public_candidate(row)

    async def list_regression_cases(
        self,
        page_no: int = 1,
        page_size: int = 30,
        status: str | None = "ACTIVE",
    ) -> dict:
        page_no = max(1, int(page_no))
        page_size = max(1, min(int(page_size), 100))
        clauses = ["1=1"]
        params: list[object] = []
        if status:
            clauses.append("status=%s")
            params.append(str(status).upper())
        where = " AND ".join(clauses)
        async with acquire() as cur:
            await cur.execute(
                f"SELECT COUNT(*) AS cnt FROM agent_regression_case WHERE {where}",
                params,
            )
            total = int((await cur.fetchone() or {}).get("cnt") or 0)
            await cur.execute(
                f"""
                SELECT * FROM agent_regression_case WHERE {where}
                ORDER BY updated_at DESC,case_id DESC LIMIT %s OFFSET %s
                """,
                [*params, page_size, (page_no - 1) * page_size],
            )
            rows = list(await cur.fetchall())
        return {
            "totalCount": total,
            "pageNo": page_no,
            "pageSize": page_size,
            "list": [_public_regression(row) for row in rows],
        }

    async def record_regression_result(
        self, case_id: int, result: str
    ) -> None:
        result = str(result or "").upper()
        if result not in {"PASS", "FAIL", "ERROR"}:
            raise ValueError("回归结果只支持 PASS/FAIL/ERROR")
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_regression_case
                SET last_result=%s,last_run_at=NOW(),updated_at=NOW()
                WHERE case_id=%s
                """,
                (result, case_id),
            )
            if cur.rowcount != 1:
                raise ValueError("回归 Case 不存在")

    async def _create_regression_case(
        self, cur, candidate: dict, regression: dict, reviewer: str
    ) -> int:
        name = str(regression.get("name") or "").strip()
        input_data = regression.get("input")
        expected = regression.get("expected")
        if not name or not isinstance(input_data, dict) or not isinstance(expected, dict):
            raise ValueError("回归 Case 需要 name、input 和 expected")
        canonical = json.dumps(
            {"input": input_data, "expected": expected},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        case_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        await cur.execute(
            """
            INSERT IGNORE INTO agent_regression_case
                (candidate_id,case_key,name,scenario,input_json,expected_json,
                 status,created_by,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,'ACTIVE',%s,NOW(),NOW())
            """,
            (
                candidate["candidate_id"],
                case_key,
                name[:255],
                str(regression.get("scenario") or candidate.get("intent") or "")[:40]
                or None,
                _json(input_data),
                _json(expected),
                reviewer[:100],
            ),
        )
        await cur.execute(
            "SELECT case_id FROM agent_regression_case WHERE case_key=%s",
            (case_key,),
        )
        case = await cur.fetchone()
        if not case:
            raise RuntimeError("regression case was not persisted")
        return int(case["case_id"])


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def _time(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value) if value else None


def _public_candidate(row: dict) -> dict:
    return {
        "candidateId": row.get("candidate_id"),
        "messageId": row.get("message_id"),
        "runId": row.get("run_id"),
        "candidateType": row.get("candidate_type"),
        "reason": row.get("reason"),
        "status": row.get("status"),
        "source": row.get("source"),
        "severity": row.get("severity"),
        "snapshot": _decode(row.get("snapshot_json")) or {},
        "labels": _decode(row.get("labels_json")) or [],
        "judge": _decode(row.get("judge_json")) or {},
        "owner": row.get("owner"),
        "fixVersion": row.get("fix_version"),
        "regressionCaseId": row.get("regression_case_id"),
        "regressionCaseKey": row.get("case_key"),
        "regressionLastResult": row.get("last_result"),
        "occurrenceCount": int(row.get("occurrence_count") or 1),
        "reviewer": row.get("reviewer"),
        "reviewRemark": row.get("review_remark"),
        "userMessage": row.get("user_message"),
        "assistantMessage": row.get("assistant_message"),
        "intent": row.get("intent"),
        "intentConfidence": (
            float(row["intent_confidence"])
            if row.get("intent_confidence") is not None
            else None
        ),
        "sentiment": row.get("sentiment"),
        "firstSeenAt": _time(row.get("first_seen_at")),
        "createdAt": _time(row.get("created_at")),
        "updatedAt": _time(row.get("updated_at")),
    }


def _public_regression(row: dict) -> dict:
    return {
        "caseId": row.get("case_id"),
        "candidateId": row.get("candidate_id"),
        "caseKey": row.get("case_key"),
        "name": row.get("name"),
        "scenario": row.get("scenario"),
        "input": _decode(row.get("input_json")) or {},
        "expected": _decode(row.get("expected_json")) or {},
        "status": row.get("status"),
        "createdBy": row.get("created_by"),
        "lastResult": row.get("last_result"),
        "lastRunAt": _time(row.get("last_run_at")),
        "createdAt": _time(row.get("created_at")),
        "updatedAt": _time(row.get("updated_at")),
    }


badcase_service = BadcaseService()
