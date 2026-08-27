"""Validated fact-level metadata used by RAG v4 retrieval and scoring."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from app.rag.canonical_facts import (
    DEFAULT_CATALOG_PATH,
    LEGACY_V1_CATALOG_PATH,
    get_canonical_fact_catalog,
)

FACT_METADATA_SCHEMA = "aishop-fact-metadata/v1"
FACT_METADATA_OVERLAY_SCHEMA = "aishop-fact-metadata-overlay/v1"
DEFAULT_FACT_METADATA_PATH = DEFAULT_CATALOG_PATH.with_name("fact-metadata.v3.json")
LEGACY_V1_FACT_METADATA_PATH = LEGACY_V1_CATALOG_PATH.with_name(
    "fact-metadata.v1.json"
)
FACT_POLARITIES = frozenset({"AFFIRMATIVE", "NEGATIVE", "MIXED"})


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_metadata_payload(
    path: Path,
    *,
    seen: frozenset[Path] = frozenset(),
) -> dict[str, Any]:
    resolved = path.resolve()
    if resolved in seen:
        raise ValueError("fact metadata overlay cycle")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"fact metadata is unreadable: {resolved}") from exc
    schema = payload.get("schemaVersion") if isinstance(payload, dict) else None
    if schema == FACT_METADATA_SCHEMA:
        return payload
    if schema != FACT_METADATA_OVERLAY_SCHEMA:
        raise ValueError("unsupported fact metadata schema")

    extension = str(payload.get("extends") or "").strip()
    if not extension or Path(extension).is_absolute():
        raise ValueError("fact metadata overlay has invalid extends")
    base = _load_metadata_payload(
        resolved.parent / extension,
        seen=seen | {resolved},
    )
    rows = json.loads(json.dumps(base.get("facts") or []))
    fact_ids = {
        str(row.get("factId") or "") for row in rows if isinstance(row, dict)
    }
    for row in payload.get("facts") or []:
        if not isinstance(row, dict):
            raise ValueError("fact metadata overlay entry must be an object")
        fact_id = str(row.get("factId") or "")
        if not fact_id or fact_id in fact_ids:
            raise ValueError(f"duplicate fact metadata overlay entry: {fact_id}")
        copied = json.loads(json.dumps(row))
        rows.append(copied)
        fact_ids.add(fact_id)
    expected_count = int(payload.get("factCount") or 0)
    if len(rows) != expected_count:
        raise ValueError(
            f"fact metadata overlay count mismatch: expected={expected_count}, actual={len(rows)}"
        )
    return {
        "schemaVersion": FACT_METADATA_SCHEMA,
        "metadataVersion": int(payload.get("metadataVersion") or 0),
        "canonicalCatalogSha256": str(
            payload.get("canonicalCatalogSha256") or ""
        ),
        "factCount": expected_count,
        "generation": str(payload.get("generation") or ""),
        "facts": rows,
    }


@dataclass(frozen=True)
class FactMetadata:
    fact_id: str
    fact_type: str
    polarity: str
    capability_boundary: bool
    domain: str
    aliases: tuple[str, ...]
    atomic_claims: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        return {
            "factId": self.fact_id,
            "factType": self.fact_type,
            "polarity": self.polarity,
            "capabilityBoundary": self.capability_boundary,
            "domain": self.domain,
            "aliases": list(self.aliases),
            "atomicClaims": list(self.atomic_claims),
        }


@dataclass(frozen=True)
class FactMetadataCatalog:
    path: Path
    catalog_sha256: str
    facts: Mapping[str, FactMetadata]

    @classmethod
    def load(
        cls,
        path: Path = DEFAULT_FACT_METADATA_PATH,
        *,
        canonical_catalog_path: Path | None = None,
    ) -> "FactMetadataCatalog":
        payload = _load_metadata_payload(path)
        metadata_version = int(payload.get("metadataVersion") or 0)
        catalog_path = canonical_catalog_path or path.with_name(
            f"catalog.v{metadata_version}.json"
        )
        catalog_sha = _sha256(catalog_path)
        if payload.get("canonicalCatalogSha256") != catalog_sha:
            raise ValueError("fact metadata canonical catalog SHA mismatch")
        rows = payload.get("facts")
        if not isinstance(rows, list):
            raise ValueError("fact metadata facts must be a list")
        facts: dict[str, FactMetadata] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("fact metadata entry must be an object")
            fact_id = str(row.get("factId") or "").strip()
            aliases = tuple(
                str(value).strip()
                for value in row.get("aliases") or []
                if str(value).strip()
            )
            claims = tuple(
                str(value).strip()
                for value in row.get("atomicClaims") or []
                if str(value).strip()
            )
            polarity = str(row.get("polarity") or "").upper()
            if (
                not fact_id
                or fact_id in facts
                or not aliases
                or not claims
                or polarity not in FACT_POLARITIES
            ):
                raise ValueError(f"invalid fact metadata entry: {fact_id!r}")
            facts[fact_id] = FactMetadata(
                fact_id=fact_id,
                fact_type=str(row.get("factType") or "POLICY").upper(),
                polarity=polarity,
                capability_boundary=bool(row.get("capabilityBoundary")),
                domain=str(row.get("domain") or "GENERAL").upper(),
                aliases=aliases,
                atomic_claims=claims,
            )
        expected = set(get_canonical_fact_catalog(catalog_path).fact_to_refs)
        if set(facts) != expected:
            missing = sorted(expected - set(facts))
            extra = sorted(set(facts) - expected)
            raise ValueError(
                f"fact metadata coverage mismatch; missing={missing}, extra={extra}"
            )
        return cls(path=path.resolve(), catalog_sha256=catalog_sha, facts=facts)


@lru_cache(maxsize=None)
def _load_fact_metadata_catalog(
    path: str, canonical_catalog_path: str
) -> FactMetadataCatalog:
    return FactMetadataCatalog.load(
        Path(path), canonical_catalog_path=Path(canonical_catalog_path)
    )


def get_fact_metadata_catalog(
    path: Path | None = None,
) -> FactMetadataCatalog:
    canonical = get_canonical_fact_catalog()
    metadata_path = Path(path) if path else canonical.path.with_name(
        f"fact-metadata.v{canonical.catalog_version}.json"
    )
    return _load_fact_metadata_catalog(
        str(metadata_path.resolve()), str(canonical.path.resolve())
    )
