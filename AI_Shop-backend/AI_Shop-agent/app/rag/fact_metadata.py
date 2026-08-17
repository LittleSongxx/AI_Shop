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
DEFAULT_FACT_METADATA_PATH = DEFAULT_CATALOG_PATH.with_name("fact-metadata.v2.json")
LEGACY_V1_FACT_METADATA_PATH = LEGACY_V1_CATALOG_PATH.with_name(
    "fact-metadata.v1.json"
)
FACT_POLARITIES = frozenset({"AFFIRMATIVE", "NEGATIVE", "MIXED"})


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schemaVersion") != FACT_METADATA_SCHEMA:
            raise ValueError("unsupported fact metadata schema")
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
        return cls(path=path, catalog_sha256=catalog_sha, facts=facts)


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
