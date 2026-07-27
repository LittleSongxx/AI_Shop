"""Generate a realistic Chinese product catalogue as SQL, using the LLM.

Why this exists: ``AI_Shop-backend/data/generate_products.sql`` titles every row
``商品-<categoryId>-<serial>``. That string contains no product word, so BM25 on
``productName`` matches nothing a user would type, dense retrieval embeds noise,
and the graded-relevance layer of ``benchmarks/run_search_relevance.py`` has
nothing it could honestly label. The catalogue is not "fake but usable" — it is
unsearchable. This script replaces it with titles a shopper would actually type.

Generation is aimed at four contracts that already exist in this repo, so the
output is consumed by the live code rather than sitting beside it:

  1. ``app/config/search_taxonomy.yml`` topics — every product belongs to one
     topic, and its title must contain at least one of that topic's ``terms``,
     because that is exactly the string normalisation hands to Elasticsearch.
  2. ``_BRAND_ALIASES`` in ``app/services/shopping_profile_service.py`` — brands
     are drawn from that tuple, so a remembered brand preference can filter.
  3. ``property_name`` rows containing 品牌 — brand extraction reads properties,
     not the title, so the brand is written to both.
  4. ``_parse_budget`` ranges — prices are spread across the bands users say out
     loud (百元内 / 千元内 / 三千内 / 更高), so a budget constraint is not vacuous.

Output is SQL, not direct inserts: the Java services own these tables, and a
reviewable .sql file keeps the Python side non-invasive.

Usage:
    python scripts/generate_catalog.py --dry-run --per-topic 2   # inspect
    python scripts/generate_catalog.py --per-topic 40            # ~480 products
    python scripts/generate_catalog.py --per-topic 100 --out ../data/02_catalog.sql
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_OUT = PROJECT_ROOT.parent / "data" / "02_catalog_seed.sql"

# Topic id -> (second-level categoryId, top-level categoryId). Must match
# ../data/01_category_seed.sql; a categoryId with no sys_category row would make
# the Java category joins return nothing.
TOPIC_CATEGORY: dict[str, tuple[str, str]] = {
    "phone": ("101", "100"),
    "computer": ("102", "100"),
    "tablet": ("103", "100"),
    "earphone": ("104", "100"),
    "camera": ("105", "100"),
    "wearable": ("106", "100"),
    "appliance": ("201", "200"),
    "snack": ("301", "300"),
    "apparel": ("401", "400"),
    "beauty": ("501", "500"),
    "toy": ("601", "600"),
    "instrument": ("602", "600"),
}

# Price bands chosen to match what _parse_budget actually recognises, so that
# "预算1000以内" is a filter that removes some rows and keeps others.
PRICE_BANDS: dict[str, tuple[float, float]] = {
    "phone": (899.0, 8999.0),
    "computer": (2999.0, 15999.0),
    "tablet": (999.0, 9999.0),
    "earphone": (79.0, 1999.0),
    "camera": (1699.0, 19999.0),
    "wearable": (149.0, 3299.0),
    "appliance": (89.0, 6999.0),
    "snack": (9.9, 129.0),
    "apparel": (39.0, 899.0),
    "beauty": (39.0, 799.0),
    "toy": (19.0, 899.0),
    "instrument": (199.0, 6999.0),
}

# Brands worth attaching per topic, drawn from _BRAND_ALIASES where one applies.
# Topics with no entry get a generic-but-plausible brand from the LLM instead.
TOPIC_BRANDS: dict[str, tuple[str, ...]] = {
    "phone": ("苹果", "华为", "小米", "红米", "荣耀", "三星", "OPPO", "vivo"),
    "computer": ("联想", "戴尔", "惠普", "苹果", "华为", "小米"),
    "tablet": ("苹果", "华为", "小米", "荣耀", "三星"),
    "earphone": ("苹果", "华为", "小米", "三星", "荣耀"),
    "wearable": ("苹果", "华为", "小米", "荣耀", "三星"),
    "apparel": ("耐克", "阿迪达斯"),
}

PROMPT = """你是电商商品库的数据编辑。为「{canonical}」这个类目生成 {count} 个真实可信的商品。

这个类目在搜索系统里注册的词是：{terms}
可用品牌：{brands}

要求：
1. 只输出 JSON 数组，不要解释、不要 markdown 代码块。
2. 每个商品的 name 必须包含上面「注册的词」里的至少一个词，否则搜索召回不到。
3. name 写成电商标题的样子：品牌 + 型号/系列 + 品类词 + 一两个卖点，20-40 字。
   不要堆砌无意义的关键词，不要写「爆款」「热卖」这类广告词。
4. 同一类目内的 {count} 个商品要有区分度：覆盖不同品牌、不同价位段、不同卖点。
5. brand 从「可用品牌」里选；如果给的是「无」，就写一个读起来像真品牌的中文名。
6. price 用人民币元，在 {price_lo} 到 {price_hi} 之间，写成数字。
   同一批里价格要拉开，低中高都要有。
7. desc 一句话说清这个商品是什么、适合谁用，30-60 字，不要复述 name。
8. scenarios 写 1-3 个使用场景词，如 办公/送礼/通勤/健身/儿童。

字段：name, brand, price, desc, scenarios

输出示例：
[{{"name":"小米 Redmi Buds 6 真无线蓝牙耳机 半入耳降噪","brand":"小米","price":149,"desc":"入门价位的真无线耳机，通勤和网课都够用，续航约 30 小时。","scenarios":["通勤","网课"]}}]
"""


def load_topics() -> list[dict[str, Any]]:
    """Read the same taxonomy file the live search path normalises against."""
    import yaml

    path = PROJECT_ROOT / "app" / "config" / "search_taxonomy.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    topics = data.get("topics") or []
    out = []
    for topic in topics:
        tid = str(topic.get("id") or "")
        if tid not in TOPIC_CATEGORY:
            # A new topic with no category mapping would generate products into a
            # category id that does not exist. Loud, not silent.
            print(
                f"  warn: topic '{tid}' has no TOPIC_CATEGORY mapping, skipped",
                file=sys.stderr,
            )
            continue
        out.append(topic)
    return out


def parse_products(raw: str) -> list[dict[str, Any]]:
    """Pull the JSON array out of an LLM reply, tolerating fences and preamble."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        payload = json.loads(text[start : end + 1])
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def title_matches_topic(name: str, terms: list[str]) -> str | None:
    """Return the topic term the title contains, or None.

    This is the whole point of the generator. ``normalize_product_search_query``
    turns a user utterance into one of these terms, and that term is what reaches
    Elasticsearch as a ``match`` on ``productName``. A generated title that
    contains no term is a row no query can ever retrieve, so it is rejected
    rather than written.
    """
    lowered = (name or "").lower()
    for term in terms:
        if term and term.lower() in lowered:
            return term
    return None


def validate(
    item: dict[str, Any],
    topic: dict[str, Any],
    band: tuple[float, float],
) -> tuple[dict[str, Any] | None, str]:
    """Coerce one generated row, or reject it with a reason."""
    name = str(item.get("name") or "").strip()
    if not name:
        return None, "empty_name"
    if len(name) > 200:  # product_info.product_name is varchar(200)
        name = name[:200]

    terms = list(topic.get("terms") or [])
    matched = title_matches_topic(name, terms)
    if matched is None:
        return None, "no_topic_term"

    try:
        price = round(float(item.get("price")), 2)
    except (TypeError, ValueError):
        return None, "bad_price"
    lo, hi = band
    # A price outside the band is not fatal, but it breaks the budget-band spread
    # the bands exist to create, so clamp rather than discard the whole row.
    price = min(max(price, lo * 0.5), hi * 1.5)
    if price <= 0:
        return None, "bad_price"

    brand = str(item.get("brand") or "").strip()[:100]
    desc = str(item.get("desc") or "").strip()
    scenarios = [
        str(s or "").strip()
        for s in (item.get("scenarios") if isinstance(item.get("scenarios"), list) else [])
    ]
    scenarios = [s for s in scenarios if s and len(s) <= 12][:3]

    return {
        "name": name,
        "brand": brand,
        "price": price,
        "desc": desc,
        "scenarios": scenarios,
        "topicId": str(topic.get("id")),
        "matchedTerm": matched,
    }, "ok"


def _cache_key(topic_id: str, count: int, batch: int, model: str) -> str:
    payload = json.dumps([topic_id, count, batch, model], sort_keys=True)
    return "mall:rag:catalog:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def generate_batch(
    topic: dict[str, Any],
    count: int,
    batch_idx: int,
    *,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Ask the LLM for one batch of products in a single topic."""
    from app.config.settings import get_settings
    from app.services.llm_factory import create_memory_llm

    settings = get_settings()
    topic_id = str(topic.get("id"))
    key = _cache_key(topic_id, count, batch_idx, settings.llm_model)

    if use_cache:
        from app.services.redis_service import redis_service

        cached = await redis_service.get_json(key)
        if isinstance(cached, list) and cached:
            return [item for item in cached if isinstance(item, dict)]

    brands = TOPIC_BRANDS.get(topic_id)
    lo, hi = PRICE_BANDS.get(topic_id, (19.0, 999.0))
    prompt = PROMPT.format(
        canonical=topic.get("canonical") or topic_id,
        count=count,
        terms="、".join(list(topic.get("terms") or [])[:20]),
        brands="、".join(brands) if brands else "无",
        price_lo=int(lo),
        price_hi=int(hi),
    )
    llm = create_memory_llm()
    response = await llm.ainvoke(prompt)
    content = getattr(response, "content", "")
    if isinstance(content, list):
        content = " ".join(str(part) for part in content)
    items = parse_products(str(content))

    if items and use_cache:
        from app.services.redis_service import redis_service

        await redis_service.set_json(key, items, settings.embedding_cache_ttl_seconds)
    return items


def sql_str(value: str) -> str:
    """Quote a string for MySQL.

    Escapes backslash first, then the quote; reversing that order would
    double-escape. Also strips control characters, which have no business in a
    product title and would corrupt the .sql file.
    """
    text = str(value or "")
    text = "".join(ch for ch in text if ch == "\t" or ch >= " ")
    text = text.replace("\\", "\\\\").replace("'", "\\'")
    return "'" + text + "'"


def emit_sql(products: list[dict[str, Any]], *, offset: int = 0) -> str:
    """Render the validated rows as a runnable, idempotent .sql file."""
    lines = [
        "-- ============================================================",
        "-- 真实感商品目录（由 scripts/generate_catalog.py 生成，勿手改）",
        "--",
        "-- 为什么需要这个文件：generate_products.sql 生成的标题是",
        "-- 「商品-101-000001」，不含任何品类词，BM25 在 productName 上匹配不到",
        "-- 用户会输入的任何词，向量化出来也是噪声。这个文件里每条标题都至少包含",
        "-- search_taxonomy.yml 里该类目注册的一个 term，也就是",
        "-- normalize_product_search_query 实际交给 ES 的那个串。",
        "--",
        "-- 依赖：先执行 01_category_seed.sql（类目 + 属性），否则外键语义上悬空。",
        "-- 库存写入 aishop_stock.sku_stock（product_sku.stock 已废弃）。",
        "-- 用法：mysql -uroot -p aishop_product < 02_catalog_seed.sql",
        "-- ============================================================",
        "",
        "SET NAMES utf8mb4;",
        "",
    ]

    info_rows: list[str] = []
    prop_rows: list[str] = []
    sku_rows: list[str] = []
    stock_rows: list[str] = []

    for idx, product in enumerate(products):
        serial = offset + idx + 1
        # varchar(15); 'G' marks generated rows so they can be deleted as a set.
        pid = f"G{serial:09d}"
        topic_id = product["topicId"]
        cat_id, p_cat_id = TOPIC_CATEGORY[topic_id]
        price = product["price"]
        # Sales skewed low with a long tail: a uniform total_sale makes every
        # popularity-ordered list arbitrary, which hides ranking bugs.
        total_sale = int(random.paretovariate(1.3) * 12) % 5000
        commend_type = 1 if random.random() < 0.12 else 0

        info_rows.append(
            "    ("
            f"{sql_str(pid)}, {sql_str(product['name'])}, {sql_str(product['desc'])}, "
            "'https://example.com/cover.png', NOW(), "
            f"{sql_str(cat_id)}, {sql_str(p_cat_id)}, 1, "
            f"{price:.2f}, {price:.2f}, {total_sale}, {commend_type})"
        )

        # Brand goes into a property row as well as the title: brand extraction
        # in shopping_profile_service reads properties, not the title.
        if product["brand"]:
            pv_id = f"PVB{serial:011d}"
            prop_rows.append(
                "    ("
                f"{sql_str(pid)}, {sql_str('P' + cat_id + '99')}, '品牌', 1, 0, "
                f"{sql_str(pv_id)}, NULL, {sql_str(product['brand'])}, NULL, 0)"
            )

        pv_hash = hashlib.md5(pid.encode("utf-8")).hexdigest()
        sku_rows.append(f"    ({sql_str(pid)}, {sql_str(pv_hash)}, '', {price:.2f}, 0)")
        stock_rows.append(f"    ({sql_str(pid)}, {sql_str(pv_hash)}, {random.randint(0, 300)})")

    def block(header: str, statement: str, rows: list[str], tail: str) -> list[str]:
        if not rows:
            return []
        out = ["-- " + header, statement]
        out.append(",\n".join(rows) + "\n" + tail)
        out.append("")
        return out

    lines += block(
        f"商品主表：{len(info_rows)} 条",
        "INSERT INTO product_info (\n"
        "    product_id, product_name, product_desc, cover, create_time,\n"
        "    category_id, p_category_id, status, min_price, max_price,\n"
        "    total_sale, commend_type\n"
        ") VALUES",
        info_rows,
        "ON DUPLICATE KEY UPDATE\n"
        "    product_name = VALUES(product_name),\n"
        "    product_desc = VALUES(product_desc),\n"
        "    min_price = VALUES(min_price),\n"
        "    max_price = VALUES(max_price);",
    )
    lines += block(
        f"品牌属性：{len(prop_rows)} 条（品牌提取读的是属性，不是标题）",
        "INSERT INTO product_property_value (\n"
        "    product_id, property_id, property_name, property_sort,\n"
        "    cover_type, property_value_id, property_cover,\n"
        "    property_value, property_remark, sort\n"
        ") VALUES",
        prop_rows,
        "ON DUPLICATE KEY UPDATE property_value = VALUES(property_value);",
    )
    lines += block(
        f"SKU：{len(sku_rows)} 条",
        "INSERT INTO product_sku (\n"
        "    product_id, property_value_id_hash, property_value_ids, price, sort\n"
        ") VALUES",
        sku_rows,
        "ON DUPLICATE KEY UPDATE price = VALUES(price);",
    )
    lines += block(
        f"库存（跨库写 aishop_stock）：{len(stock_rows)} 条",
        "INSERT INTO aishop_stock.sku_stock (product_id, property_value_id_hash, stock) VALUES",
        stock_rows,
        "ON DUPLICATE KEY UPDATE stock = VALUES(stock);",
    )

    lines += [
        "-- 回滚：DELETE FROM product_info WHERE product_id LIKE 'G%';",
        "--       DELETE FROM product_sku  WHERE product_id LIKE 'G%';",
        "--       DELETE FROM product_property_value WHERE product_id LIKE 'G%';",
        "--       DELETE FROM aishop_stock.sku_stock WHERE product_id LIKE 'G%';",
        "",
    ]
    return "\n".join(lines)


async def collect_topic(
    topic: dict[str, Any],
    args: argparse.Namespace,
    stats: dict[str, int],
    rejects: dict[str, int],
    lock: asyncio.Lock,
) -> list[dict[str, Any]]:
    """Generate and validate one topic's products, in batches with dedupe."""
    topic_id = str(topic.get("id"))
    band = PRICE_BANDS.get(topic_id, (19.0, 999.0))
    wanted = args.per_topic
    accepted: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    batch_idx = 0
    # Cap the retries: without it, a topic the model keeps failing would loop and
    # silently burn tokens.
    max_batches = max(2, (wanted // max(1, args.batch_size)) + 3)
    while len(accepted) < wanted and batch_idx < max_batches:
        need = min(args.batch_size, wanted - len(accepted))
        try:
            items = await generate_batch(topic, need, batch_idx, use_cache=not args.no_cache)
        except Exception as exc:
            stats["llm_failed"] += 1
            async with lock:
                print(
                    f"  warn: LLM failed for topic {topic_id} batch {batch_idx}: "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
            break
        batch_idx += 1
        if not items:
            stats["empty_batch"] += 1
            continue

        for item in items:
            if len(accepted) >= wanted:
                break
            product, reason = validate(item, topic, band)
            if product is None:
                rejects[reason] = rejects.get(reason, 0) + 1
                continue
            key = product["name"]
            if key in seen_names:
                rejects["duplicate_name"] = rejects.get("duplicate_name", 0) + 1
                continue
            seen_names.add(key)
            accepted.append(product)

    async with lock:
        marker = "" if len(accepted) >= wanted else f"  (asked {wanted})"
        print(f"  {topic_id:<11} {len(accepted):>4} products{marker}")
    stats["accepted"] += len(accepted)
    return accepted


def summarize_bands(products: list[dict[str, Any]]) -> dict[str, int]:
    """Count products per budget band users actually say out loud.

    Prints so a run that produced 900 products all above 3000 is visible: a
    budget constraint over such a catalogue removes everything or nothing.
    """
    bands = {"<=100": 0, "100-1000": 0, "1000-3000": 0, ">3000": 0}
    for product in products:
        price = product["price"]
        if price <= 100:
            bands["<=100"] += 1
        elif price <= 1000:
            bands["100-1000"] += 1
        elif price <= 3000:
            bands["1000-3000"] += 1
        else:
            bands[">3000"] += 1
    return bands


async def run(args: argparse.Namespace) -> int:
    random.seed(args.seed)

    from app.services.redis_service import redis_service

    if not args.no_cache:
        await redis_service.ensure_connected()

    topics = load_topics()
    if not topics:
        print("No usable topics in search_taxonomy.yml.", file=sys.stderr)
        return 1

    stats = {"accepted": 0, "llm_failed": 0, "empty_batch": 0}
    rejects: dict[str, int] = {}
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    print(f"generating {args.per_topic} products x {len(topics)} topics")

    async def guarded(topic: dict[str, Any]) -> list[dict[str, Any]]:
        async with semaphore:
            return await collect_topic(topic, args, stats, rejects, lock)

    try:
        results = await asyncio.gather(*(guarded(topic) for topic in topics))
    finally:
        if not args.no_cache:
            await redis_service.close()

    products = [product for group in results for product in group]
    if not products:
        print("Nothing generated; refusing to write an empty .sql", file=sys.stderr)
        return 1

    print("\n" + json.dumps(stats, ensure_ascii=False, indent=2))
    if rejects:
        print("rejected: " + json.dumps(rejects, ensure_ascii=False, sort_keys=True))
    print("price bands: " + json.dumps(summarize_bands(products), ensure_ascii=False))

    sql = emit_sql(products, offset=args.id_offset)
    if args.dry_run:
        print("\n" + "=" * 60)
        for product in products[:10]:
            print(
                f"  [{product['topicId']:<10}] {product['price']:>9.2f}  "
                f"{product['name']}   (命中词: {product['matchedTerm']})"
            )
        if len(products) > 10:
            print(f"  ... {len(products) - 10} more")
        print("=" * 60)
        print(f"\n--dry-run: would write {len(sql.splitlines())} lines to {args.out}")
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sql, encoding="utf-8")
    print(f"\nwrote {len(products)} products to {out}")
    print(f"next: mysql aishop_product < {out.name}  然后重建索引（scripts/vector_index.py）")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a realistic Chinese product catalogue as SQL.",
    )
    parser.add_argument(
        "--per-topic",
        type=int,
        default=40,
        help="Products per taxonomy topic. 12 topics, so 40 gives ~480 products.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Products requested per LLM call. Large batches degrade in variety.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Parallel topics. Keep low to respect provider rate limits.",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"Output .sql path (default: {DEFAULT_OUT}).",
    )
    parser.add_argument(
        "--id-offset",
        type=int,
        default=0,
        help="Start product ids after this serial, to append without collisions.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260727,
        help="Seed for sales/stock jitter, so reruns are reproducible.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print a sample and the stats instead of writing the .sql file.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the Redis batch cache and re-pay for every LLM call.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
