"""Durable Idempotency-Key semantics for the public Agent message endpoint.

The message table remains the conversation history and the task table remains
the execution ledger. This small request ledger owns the API contract: one
authenticated user and one key can reserve exactly one payload, while replaying
the same payload returns the original response and a changed payload is a
conflict. A reservation is created before rate limiting or any user-memory
mutation, so concurrent retries cannot create two messages or tasks.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from pymysql.err import OperationalError

from app.db.pool import acquire, transaction

_TABLE = "agent_request_idempotency"
_RESERVED = "RESERVED"
_COMPLETED = "COMPLETED"
_FAILED = "FAILED"
_INCONCLUSIVE = "INCONCLUSIVE"
_TERMINAL = frozenset({_COMPLETED, _FAILED, _INCONCLUSIVE})


class AgentRequestIdempotencyConflict(ValueError):
    """The authenticated user reused a key for a different payload."""


class AgentRequestIdempotencyLedgerError(RuntimeError):
    """The durable response ledger could not confirm a state transition."""


@dataclass(frozen=True)
class IdempotencyReservation:
    user_id: str
    key: str
    fingerprint: str
    run_id: str
    owner: bool
    state: str
    message_id: int | None = None
    response: dict[str, Any] | None = None

    @property
    def resolved(self) -> bool:
        return self.response is not None and self.state in _TERMINAL


def _decode_response(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return None
    return dict(decoded) if isinstance(decoded, Mapping) else None


def _row_reservation(
    row: Mapping[str, Any],
    *,
    user_id: str,
    key: str,
    fingerprint: str,
    owner: bool,
) -> IdempotencyReservation:
    stored_fingerprint = str(row.get("request_fingerprint") or "").lower()
    if stored_fingerprint != fingerprint:
        raise AgentRequestIdempotencyConflict(
            "Idempotency-Key 已用于不同请求"
        )
    message_id = row.get("message_id")
    return IdempotencyReservation(
        user_id=user_id,
        key=key,
        fingerprint=fingerprint,
        run_id=str(row.get("run_id") or ""),
        owner=owner,
        state=str(row.get("status") or _RESERVED),
        message_id=int(message_id) if message_id is not None else None,
        response=_decode_response(row.get("response_json")),
    )


class AgentRequestIdempotencyService:
    MAX_KEY_LENGTH = 160

    @classmethod
    def normalize_key(cls, value: str | None) -> str:
        key = str(value or "").strip()
        if not key or len(key) > cls.MAX_KEY_LENGTH:
            raise ValueError("Idempotency-Key 长度必须为 1 到 160")
        return key

    @staticmethod
    def fingerprint(
        *,
        message: str,
        from_product: bool,
        consult_product_id: str | None,
        comparison_product_ids: list[str] | None,
        image_asset_id: str | None,
    ) -> str:
        payload = {
            "schema": "agent-send-message/v1",
            "message": str(message or "").strip(),
            "fromProduct": bool(from_product),
            "consultProductId": str(consult_product_id or "").strip() or None,
            "comparisonProductIds": [
                str(item).strip()
                for item in (comparison_product_ids or [])
                if str(item or "").strip()
            ],
            "imageAssetId": str(image_asset_id or "").strip() or None,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def run_id(user_id: str, key: str) -> str:
        digest = hashlib.sha256(
            f"agent-send\0{str(user_id).strip()}\0{key}".encode("utf-8")
        ).hexdigest()
        # agent_run.run_id is varchar(64); the deterministic prefix makes the
        # provenance visible without exposing the caller's raw key.
        return "idem_" + digest[:59]

    async def reserve(
        self,
        *,
        user_id: str,
        key: str,
        fingerprint: str,
    ) -> IdempotencyReservation:
        normalized_user = str(user_id or "").strip()
        normalized_key = self.normalize_key(key)
        normalized_fingerprint = str(fingerprint or "").strip().lower()
        if not normalized_user:
            raise ValueError("用户身份不能为空")
        if len(normalized_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in normalized_fingerprint
        ):
            raise ValueError("request fingerprint 必须是 SHA-256 十六进制")
        run_id = self.run_id(normalized_user, normalized_key)
        owner = False
        # Keep the insert transaction short. Holding an INSERT-duplicate lock
        # while doing a SELECT ... FOR UPDATE creates an InnoDB deadlock when
        # several processes reserve the same key at once.
        for attempt in range(3):
            try:
                async with transaction() as cur:
                    await cur.execute(
                        f"""
                        INSERT IGNORE INTO {_TABLE}
                            (user_id, idempotency_key, request_fingerprint, run_id,
                             status, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, NOW(3), NOW(3))
                        """,
                        (
                            normalized_user,
                            normalized_key,
                            normalized_fingerprint,
                            run_id,
                            _RESERVED,
                        ),
                    )
                    owner = int(cur.rowcount or 0) == 1
                break
            except OperationalError as exc:
                # 1213 = deadlock; 1205 = lock wait timeout. Both are safe to
                # retry because the reservation insert is deterministic and
                # has no user-visible side effect until it commits.
                if int(exc.args[0] if exc.args else 0) not in (1205, 1213):
                    raise
                if attempt == 2:
                    raise
                await asyncio.sleep(0.02 * (attempt + 1))

        async with acquire() as cur:
            await cur.execute(
                f"""
                SELECT user_id, idempotency_key, request_fingerprint, run_id,
                       message_id, status, response_json
                FROM {_TABLE}
                WHERE user_id=%s AND idempotency_key=%s
                """,
                (normalized_user, normalized_key),
            )
            row = await cur.fetchone()
            if not row:
                raise RuntimeError("idempotency reservation disappeared")
        return _row_reservation(
            row,
            user_id=normalized_user,
            key=normalized_key,
            fingerprint=normalized_fingerprint,
            owner=owner,
        )

    async def complete(
        self,
        reservation: IdempotencyReservation,
        response: Mapping[str, Any],
        *,
        message_id: int | None = None,
    ) -> None:
        payload = json.dumps(
            dict(response), ensure_ascii=False, sort_keys=True, default=str
        )
        async with acquire() as cur:
            await cur.execute(
                f"""
                UPDATE {_TABLE}
                SET status=%s, message_id=COALESCE(%s, message_id),
                    response_json=%s, updated_at=NOW(3)
                WHERE user_id=%s AND idempotency_key=%s
                  AND request_fingerprint=%s
                  AND status=%s
                """,
                (
                    _COMPLETED,
                    message_id,
                    payload,
                    reservation.user_id,
                    reservation.key,
                    reservation.fingerprint,
                    _RESERVED,
                ),
            )
            if int(cur.rowcount or 0) == 1:
                return
            # A duplicate owner must never overwrite a terminal response.  It
            # is safe to treat an already-published identical response as a
            # successful replay; every other outcome means the ledger state is
            # no longer the state this owner reserved.
            await cur.execute(
                f"""
                SELECT user_id, idempotency_key, request_fingerprint, run_id,
                       message_id, status, response_json
                FROM {_TABLE}
                WHERE user_id=%s AND idempotency_key=%s
                  AND request_fingerprint=%s
                FOR UPDATE
                """,
                (reservation.user_id, reservation.key, reservation.fingerprint),
            )
            row = await cur.fetchone()
            current = _row_reservation(
                row,
                user_id=reservation.user_id,
                key=reservation.key,
                fingerprint=reservation.fingerprint,
                owner=False,
            ) if row else None
            if current and current.state == _COMPLETED and current.response == dict(response):
                return
            raise AgentRequestIdempotencyLedgerError(
                "idempotency response ledger completion was not confirmed"
            )

    async def fail(
        self,
        reservation: IdempotencyReservation,
        response: Mapping[str, Any],
    ) -> None:
        payload = json.dumps(
            dict(response), ensure_ascii=False, sort_keys=True, default=str
        )
        async with acquire() as cur:
            await cur.execute(
                f"""
                UPDATE {_TABLE}
                SET status=%s, response_json=%s, updated_at=NOW(3)
                WHERE user_id=%s AND idempotency_key=%s
                  AND request_fingerprint=%s
                  AND status=%s
                """,
                (
                    _FAILED,
                    payload,
                    reservation.user_id,
                    reservation.key,
                    reservation.fingerprint,
                    _RESERVED,
                ),
            )
            if int(cur.rowcount or 0) == 1:
                return
            await cur.execute(
                f"""
                SELECT user_id, idempotency_key, request_fingerprint, run_id,
                       message_id, status, response_json
                FROM {_TABLE}
                WHERE user_id=%s AND idempotency_key=%s
                  AND request_fingerprint=%s
                FOR UPDATE
                """,
                (reservation.user_id, reservation.key, reservation.fingerprint),
            )
            row = await cur.fetchone()
            current = _row_reservation(
                row,
                user_id=reservation.user_id,
                key=reservation.key,
                fingerprint=reservation.fingerprint,
                owner=False,
            ) if row else None
            if current and current.state == _FAILED and current.response == dict(response):
                return
            raise AgentRequestIdempotencyLedgerError(
                "idempotency response ledger failure was not confirmed"
            )

    async def inconclusive(
        self,
        reservation: IdempotencyReservation,
        response: Mapping[str, Any],
        *,
        message_id: int | None = None,
    ) -> None:
        """Publish an explicit unknown outcome without overwriting a terminal row."""
        payload = json.dumps(
            dict(response), ensure_ascii=False, sort_keys=True, default=str
        )
        async with acquire() as cur:
            await cur.execute(
                f"""
                UPDATE {_TABLE}
                SET status=%s, message_id=COALESCE(%s, message_id),
                    response_json=%s, updated_at=NOW(3)
                WHERE user_id=%s AND idempotency_key=%s
                  AND request_fingerprint=%s
                  AND status=%s
                """,
                (
                    _INCONCLUSIVE,
                    message_id,
                    payload,
                    reservation.user_id,
                    reservation.key,
                    reservation.fingerprint,
                    _RESERVED,
                ),
            )
            if int(cur.rowcount or 0) == 1:
                return
            await cur.execute(
                f"""
                SELECT user_id, idempotency_key, request_fingerprint, run_id,
                       message_id, status, response_json
                FROM {_TABLE}
                WHERE user_id=%s AND idempotency_key=%s
                  AND request_fingerprint=%s
                """,
                (reservation.user_id, reservation.key, reservation.fingerprint),
            )
            row = await cur.fetchone()
            current = _row_reservation(
                row,
                user_id=reservation.user_id,
                key=reservation.key,
                fingerprint=reservation.fingerprint,
                owner=False,
            ) if row else None
            if (
                current
                and current.state == _INCONCLUSIVE
                and current.response == dict(response)
            ):
                return
            raise AgentRequestIdempotencyLedgerError(
                "idempotency inconclusive outcome was not confirmed"
            )

    async def wait(self, reservation: IdempotencyReservation, timeout: float = 8.0) -> IdempotencyReservation:
        """Wait for the owner to publish a replayable response, then re-read it."""
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
        while True:
            async with acquire() as cur:
                await cur.execute(
                    f"""
                    SELECT user_id, idempotency_key, request_fingerprint, run_id,
                           message_id, status, response_json
                    FROM {_TABLE}
                    WHERE user_id=%s AND idempotency_key=%s
                    """,
                    (reservation.user_id, reservation.key),
                )
                row = await cur.fetchone()
            if row:
                current = _row_reservation(
                    row,
                    user_id=reservation.user_id,
                    key=reservation.key,
                    fingerprint=reservation.fingerprint,
                    owner=False,
                )
                if current.resolved:
                    return current
                reservation = current
            if asyncio.get_running_loop().time() >= deadline:
                return reservation
            await asyncio.sleep(0.1)


agent_request_idempotency_service = AgentRequestIdempotencyService()
