from __future__ import annotations

import hashlib
import json
from typing import Iterable

import redis

from evaluation.text2sql.contracts import Actor, Text2SqlCase
from evaluation.text2sql.fixture import REDIS_PORT

PERMISSION_MAP = {
    "ANALYTICS_READ": "analytics:read",
    "ANALYTICS_EXPORT": "analytics:export",
}


def _token(actor: Actor) -> str:
    material = f"text2sql-v0:{actor.admin_id}:{actor.role}:{','.join(sorted(actor.permissions))}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]


def seed_admin_sessions(
    cases: Iterable[Text2SqlCase],
    *,
    ttl_seconds: int = 3600,
) -> dict[str, str]:
    client = redis.Redis(host="127.0.0.1", port=REDIS_PORT, db=0)
    actors = {case.actor.admin_id: case.actor for case in cases}
    tokens: dict[str, str] = {}
    for admin_id, actor in actors.items():
        token = _token(actor)
        principal = {
            "@class": "com.aishop.entity.dto.AdminPrincipalDTO",
            "adminId": admin_id,
            "account": admin_id,
            "displayName": f"Text2SQL evaluation {admin_id}",
            "roles": ["java.util.LinkedHashSet", [actor.role]],
            "permissions": [
                "java.util.LinkedHashSet",
                [PERMISSION_MAP[item] for item in actor.permissions if item in PERMISSION_MAP],
            ],
            "sessionVersion": 1,
        }
        client.set(
            f"mall:token:admin:{token}",
            json.dumps(principal, ensure_ascii=False, separators=(",", ":")),
            ex=ttl_seconds,
        )
        client.set(f"mall:admin:session-version:{admin_id}", "1", ex=ttl_seconds)
        tokens[admin_id] = token
    return tokens
