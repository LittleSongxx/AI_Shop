"""Build deterministic fact-metadata.v1.json from the published catalog."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.canonical_facts import DEFAULT_CATALOG_PATH  # noqa: E402
from app.rag.fact_metadata import (  # noqa: E402
    DEFAULT_FACT_METADATA_PATH,
    FACT_METADATA_SCHEMA,
)

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_SENTENCE_RE = re.compile(r"[。；;\n]")
_NEGATIVE_RE = re.compile(r"(?:不支持|不能|不得|不会|不可|不允许|仅|无法|无权)")
_CAPABILITY_PREFIXES = ("ai.", "payment.", "privacy.", "review.ai_")


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Replace generated metadata atomically without an evaluation dependency."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _sections(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    matches = list(_HEADING_RE.finditer(text))
    return {
        match.group(1).strip(): text[
            match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(text)
        ].strip()
        for index, match in enumerate(matches)
    }


def _claims(body: str) -> list[str]:
    result: list[str] = []
    for value in _SENTENCE_RE.split(body):
        cleaned = re.sub(r"^[-*]\s*", "", value).strip()
        if cleaned and not cleaned.startswith("#") and cleaned not in result:
            result.append(cleaned[:300])
        if len(result) == 4:
            break
    return result


def build(output: Path = DEFAULT_FACT_METADATA_PATH) -> dict:
    catalog = json.loads(DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))
    by_fact: dict[str, dict] = {}
    for document in catalog.get("documents") or []:
        source = DEFAULT_CATALOG_PATH.parent / str(document["file"])
        section_bodies = _sections(source)
        for section in document.get("sections") or []:
            fact_id = str(section["factId"])
            heading = str(section["heading"])
            claims = _claims(section_bodies.get(heading, ""))
            if not claims:
                raise ValueError(f"knowledge section has no atomic claims: {source.name}#{heading}")
            has_negative = bool(_NEGATIVE_RE.search("。".join(claims)))
            direct_negative = "no_" in fact_id or heading.startswith(("不支持", "禁止"))
            polarity = "NEGATIVE" if direct_negative else "MIXED" if has_negative else "AFFIRMATIVE"
            prefix = fact_id.split(".", 1)[0].upper()
            row = {
                "factId": fact_id,
                "factType": "CAPABILITY" if fact_id.startswith(_CAPABILITY_PREFIXES) else prefix,
                "polarity": polarity,
                "capabilityBoundary": fact_id.startswith(_CAPABILITY_PREFIXES) and has_negative,
                "domain": str(document.get("domain") or "GENERAL").upper(),
                "aliases": [heading, f"{document.get('title')} {heading}"],
                "atomicClaims": claims,
            }
            existing = by_fact.get(fact_id)
            if existing is None:
                by_fact[fact_id] = row
            else:
                existing["aliases"] = list(dict.fromkeys([*existing["aliases"], *row["aliases"]]))
                existing["atomicClaims"] = list(
                    dict.fromkeys([*existing["atomicClaims"], *row["atomicClaims"]])
                )[:8]
                if existing["polarity"] != row["polarity"]:
                    existing["polarity"] = "MIXED"
                existing["capabilityBoundary"] = bool(
                    existing["capabilityBoundary"] or row["capabilityBoundary"]
                )
    rows = list(by_fact.values())
    payload = {
        "schemaVersion": FACT_METADATA_SCHEMA,
        "metadataVersion": 1,
        "canonicalCatalogSha256": hashlib.sha256(DEFAULT_CATALOG_PATH.read_bytes()).hexdigest(),
        "factCount": len(rows),
        "generation": "deterministic headings and published section sentences; no LLM",
        "facts": sorted(rows, key=lambda row: row["factId"]),
    }
    _atomic_write_json(output, payload)
    return payload


if __name__ == "__main__":
    result = build()
    print(json.dumps({"path": str(DEFAULT_FACT_METADATA_PATH), "facts": result["factCount"]}))
