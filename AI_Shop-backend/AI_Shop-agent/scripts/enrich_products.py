"""Offline LLM enrichment of product documents in the vector index.

Why this exists: product titles are the only thing dense retrieval has to work
with, and a bare title carries no synonyms ("无线耳机" vs "蓝牙耳机"), no usage
scenario ("送礼", "办公") and no audience. Enriching the embedded text with those
terms widens recall without touching the ranker or the Java services.

Writes go to a separate document id namespace (``enriched-product-<id>``) so the
base documents Spring AI maintains are never clobbered; both carry
``metadata.dataType = "product"`` and so both are visible to the same kNN filter.

SAFETY: given a placeholder title like "商品-105-00001" an LLM will happily
invent specifications, which then get embedded and produce confident false
matches. Products whose titles carry no real signal are skipped unless
``--allow-placeholder-names`` is passed explicitly.

Usage:
    python scripts/enrich_products.py --dry-run --limit 5     # inspect prompts+output
    python scripts/enrich_products.py --limit 50              # small paid pilot
    python scripts/enrich_products.py                         # full run, resumable
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PRODUCT_INDEX = "aishop-index"
ENRICHED_ID_PREFIX = "enriched-product-"
STATE_FILE = Path(__file__).with_name("enrich_products.state.jsonl")

# "商品-105-00001" and friends: a category id and a serial, no product semantics.
_PLACEHOLDER_NAME_RE = re.compile(r"^商品[-_\s]*\d+[-_\s]*\d+$")

PROMPT = """你是电商搜索的商品理解助手。根据给定商品信息，输出用于提升搜索召回的结构化补充信息。

商品名称：{name}
商品描述：{desc}
商品规格：{props}

要求：
1. 只输出 JSON，不要解释、不要 markdown 代码块。
2. 不要编造给定信息里没有依据的参数（型号、容量、材质等）。信息不足时给空数组。
3. 字段：
   - synonyms: 同义词/别称/口语说法，用户可能用来搜这个商品的词，最多 8 个
   - attributes: 从名称与规格中提取的关键属性词，最多 8 个
   - scenarios: 适用场景，如 办公/送礼/通勤/儿童，最多 5 个
   - audiences: 目标人群，如 学生/老人/女生，最多 4 个

输出示例：
{{"synonyms":["蓝牙耳机","无线耳机"],"attributes":["降噪","入耳式"],"scenarios":["通勤","办公"],"audiences":["学生"]}}
"""

_FIELD_LABELS = (
    ("synonyms", "别称"),
    ("attributes", "属性"),
    ("scenarios", "适用场景"),
    ("audiences", "适用人群"),
)


def is_placeholder_name(name: str) -> bool:
    value = (name or "").strip()
    if not value:
        return True
    return bool(_PLACEHOLDER_NAME_RE.match(value))


def _es_base() -> str:
    from app.config.settings import get_settings

    return get_settings().es_hosts.split(",")[0].rstrip("/")


async def scan_product_ids(limit: int | None) -> list[str]:
    """Page through the keyword index for product ids, newest-agnostic order.

    ``search_after`` on _doc is used rather than the scroll API so no server-side
    context is held between pages; this loop is safe to interrupt.
    """
    from app.infra.http_client import get_client

    client = await get_client("es", timeout=30)
    base = _es_base()
    page = 500 if limit is None else min(500, max(limit, 1))
    ids: list[str] = []
    search_after: list[Any] | None = None
    while True:
        body: dict[str, Any] = {
            "size": page,
            "query": {"match_all": {}},
            "sort": [{"_doc": "asc"}],
            "_source": ["productId"],
        }
        if search_after is not None:
            body["search_after"] = search_after
        resp = await client.post(f"{base}/{PRODUCT_INDEX}/_search", json=body, timeout=30)
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
        if not hits:
            break
        for hit in hits:
            pid = (hit.get("_source") or {}).get("productId")
            if pid:
                ids.append(str(pid))
        if limit is not None and len(ids) >= limit:
            return ids[:limit]
        search_after = hits[-1].get("sort")
        if not search_after:
            break
    return ids


def _format_props(rows: list[dict]) -> str:
    parts = []
    for row in rows or []:
        name = row.get("property_name") or row.get("propertyName")
        value = row.get("property_value") or row.get("propertyValue")
        if name and value:
            parts.append(f"{name}:{value}")
    return "，".join(parts[:12]) or "无"


async def load_products(product_ids: list[str], batch_size: int = 50) -> list[dict]:
    """Fetch names, descriptions and properties for the given ids."""
    from app.services.java_internal_client import java_internal_client

    out: list[dict] = []
    for start in range(0, len(product_ids), batch_size):
        chunk = product_ids[start : start + batch_size]
        batch = await java_internal_client.snapshot_batch(chunk)
        if not batch or not isinstance(batch.get("products"), list):
            print(f"  warn: snapshot_batch returned nothing for {len(chunk)} ids", file=sys.stderr)
            continue
        props_by_product: dict[str, list[dict]] = {}
        for prop in batch.get("property_values") or []:
            pid = str(prop.get("product_id") or "")
            if pid:
                props_by_product.setdefault(pid, []).append(prop)
        for row in batch["products"]:
            pid = str(row.get("product_id") or "")
            if not pid:
                continue
            out.append(
                {
                    "productId": pid,
                    "name": str(row.get("product_name") or "").strip(),
                    "desc": str(row.get("product_desc") or "").strip(),
                    "categoryId": str(row.get("category_id") or ""),
                    "props": _format_props(props_by_product.get(pid) or []),
                }
            )
    return out


def _clean_list(value: Any, cap: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        # Long entries are prose, not tags, and only dilute the embedding.
        if not text or len(text) > 12 or text in out:
            continue
        out.append(text)
    return out[:cap]


def parse_enrichment(raw: str) -> dict[str, list[str]]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        payload = json.loads(text[start : end + 1])
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "synonyms": _clean_list(payload.get("synonyms"), 8),
        "attributes": _clean_list(payload.get("attributes"), 8),
        "scenarios": _clean_list(payload.get("scenarios"), 5),
        "audiences": _clean_list(payload.get("audiences"), 4),
    }


def _cache_key(product: dict, model: str) -> str:
    payload = json.dumps(
        [product.get("name"), product.get("desc"), product.get("props"), model],
        ensure_ascii=False,
        sort_keys=True,
    )
    return "mall:rag:enrich:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def enrich_one(product: dict, *, use_cache: bool = True) -> dict[str, list[str]]:
    """Ask the LLM for search-side synonyms and tags, caching by product content."""
    from app.config.settings import get_settings
    from app.services.llm_factory import create_memory_llm
    from app.services.redis_service import redis_service

    model = get_settings().llm_model
    key = _cache_key(product, model)
    if use_cache:
        cached = await redis_service.get_json(key)
        if isinstance(cached, dict):
            return cached

    prompt = PROMPT.format(
        name=product.get("name") or "未知",
        desc=(product.get("desc") or "无")[:400],
        props=product.get("props") or "无",
    )
    llm = create_memory_llm()
    response = await llm.ainvoke(prompt)
    content = getattr(response, "content", "")
    if isinstance(content, list):
        content = " ".join(str(part) for part in content)
    parsed = parse_enrichment(str(content))
    if parsed and any(parsed.values()) and use_cache:
        await redis_service.set_json(key, parsed, get_settings().embedding_cache_ttl_seconds)
    return parsed


def build_enriched_text(product: dict, enrichment: dict[str, list[str]]) -> str:
    """Compose the text that actually gets embedded."""
    lines = [f"商品名称：{product.get('name') or ''}"]
    desc = (product.get("desc") or "").strip()
    if desc:
        lines.append(f"商品描述：{desc[:300]}")
    props = (product.get("props") or "").strip()
    if props and props != "无":
        lines.append(f"规格：{props}")
    for field, label in _FIELD_LABELS:
        values = enrichment.get(field) or []
        if values:
            lines.append(f"{label}：{'、'.join(values)}")
    return "\n".join(lines)


async def upsert_enriched_doc(
    product: dict,
    text: str,
    enrichment: dict[str, list[str]],
) -> bool:
    """Embed the enriched text and write it under the enriched-id namespace."""
    from app.config.settings import get_settings
    from app.infra.http_client import get_client
    from app.rag.embedding import embed_text

    vector = await embed_text(text)
    if not vector:
        print(f"  warn: embedding failed for {product['productId']}", file=sys.stderr)
        return False

    settings = get_settings()
    doc = {
        "content": text,
        "metadata": {
            "dataType": "product",
            "productId": product["productId"],
            "categoryId": product.get("categoryId") or "",
            "enriched": True,
            "synonyms": enrichment.get("synonyms") or [],
            "scenarios": enrichment.get("scenarios") or [],
        },
        settings.es_vector_field: vector,
    }
    client = await get_client("es", timeout=30)
    doc_id = f"{ENRICHED_ID_PREFIX}{product['productId']}"
    resp = await client.put(
        f"{_es_base()}/{settings.es_index}/_doc/{doc_id}",
        json=doc,
        timeout=30,
    )
    resp.raise_for_status()
    return True


async def process_product(
    product: dict,
    args: argparse.Namespace,
    stats: dict[str, int],
    done: set[str],
    state_handle: Any,
    lock: asyncio.Lock,
) -> None:
    pid = product["productId"]
    if pid in done:
        stats["skipped_done"] += 1
        return
    if not args.allow_placeholder_names and is_placeholder_name(product.get("name") or ""):
        stats["skipped_placeholder"] += 1
        return

    try:
        enrichment = await enrich_one(product, use_cache=not args.no_cache)
    except Exception as exc:
        stats["llm_failed"] += 1
        print(f"  warn: LLM failed for {pid}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if not enrichment or not any(enrichment.values()):
        stats["empty_enrichment"] += 1
        return

    text = build_enriched_text(product, enrichment)
    if args.dry_run:
        stats["would_index"] += 1
        async with lock:
            print(f"\n--- {pid} :: {product.get('name')}")
            print(text)
        return

    try:
        if await upsert_enriched_doc(product, text, enrichment):
            stats["indexed"] += 1
        else:
            stats["index_failed"] += 1
            return
    except Exception as exc:
        stats["index_failed"] += 1
        print(f"  warn: index failed for {pid}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return

    if state_handle is not None:
        async with lock:
            state_handle.write(json.dumps({"productId": pid}, ensure_ascii=False) + "\n")
            state_handle.flush()


def load_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = json.loads(line).get("productId")
        except (TypeError, ValueError):
            continue
        if pid:
            done.add(str(pid))
    return done


async def run(args: argparse.Namespace) -> int:
    from app.services.redis_service import redis_service

    await redis_service.ensure_connected()
    stats = {
        "scanned": 0,
        "loaded": 0,
        "indexed": 0,
        "would_index": 0,
        "skipped_done": 0,
        "skipped_placeholder": 0,
        "empty_enrichment": 0,
        "llm_failed": 0,
        "index_failed": 0,
    }
    done = set() if args.no_resume else load_state(STATE_FILE)
    if done:
        print(f"resuming: {len(done)} products already indexed")

    product_ids = await scan_product_ids(args.limit)
    stats["scanned"] = len(product_ids)
    if not product_ids:
        print(
            f"No products found in '{PRODUCT_INDEX}'. Is the search index built?",
            file=sys.stderr,
        )
        return 1

    products = await load_products(product_ids)
    stats["loaded"] = len(products)
    print(f"scanned {stats['scanned']} ids, loaded {stats['loaded']} products")

    state_handle = None
    if not args.dry_run:
        state_handle = STATE_FILE.open("a", encoding="utf-8")
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    async def guarded(product: dict) -> None:
        async with semaphore:
            await process_product(product, args, stats, done, state_handle, lock)

    try:
        await asyncio.gather(*(guarded(product) for product in products))
    finally:
        if state_handle is not None:
            state_handle.close()
        await redis_service.close()

    print("\n" + json.dumps(stats, ensure_ascii=False, indent=2))
    if stats["skipped_placeholder"]:
        print(
            f"\nNOTE: skipped {stats['skipped_placeholder']} products whose titles carry no\n"
            "product semantics (e.g. '商品-105-00001'). Enriching those would have the LLM\n"
            "invent specifications, which then get embedded as confident false matches.\n"
            "Load a real catalogue, or pass --allow-placeholder-names if you accept that.",
            file=sys.stderr,
        )
    return 0 if not stats["llm_failed"] and not stats["index_failed"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LLM-enrich product documents in the vector index.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N products. Use for a cheap paid pilot.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Parallel LLM+embedding calls. Keep low to respect provider rate limits.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the enriched text instead of writing to Elasticsearch.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the Redis enrichment cache and re-pay for every LLM call.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore the state file and reprocess everything.",
    )
    parser.add_argument(
        "--allow-placeholder-names",
        action="store_true",
        help="Enrich products whose titles carry no semantics. Risks hallucinated specs.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
