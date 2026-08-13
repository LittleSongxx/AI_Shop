"""Deterministic WANDS download, verification and judged-pool selection."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from benchmarks.mature_eval.common import atomic_write_bytes, atomic_write_json, sha256_bytes

WANDS_COMMIT = "3b74dcf4ba29ab8ff3e6a50b5b09fc627cb882b5"
WANDS_BASE_URL = f"https://raw.githubusercontent.com/wayfair/WANDS/{WANDS_COMMIT}/dataset"
WANDS_SOURCE_SHA256 = {
    "label.csv": "c11fe81ad62f17f56f316b0ec9630ebe8fbe1393578cb0ca4f05c17253a180ef",
    "product.csv": "d993926254572e6eba96c8fd87cc549a17fb91ad3748308036eee4cf92b10ac6",
    "query.csv": "63b61660560fecc33ec490804c7e2b81402ee3e7c31a9cbb5e03736639f68e95",
}
LABEL_GRADES = {"Exact": 2, "Partial": 1, "Irrelevant": 0}


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=90) as response:  # noqa: S310 - fixed HTTPS host
        return response.read()


def download_sources(raw_dir: Path) -> dict[str, Path]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    for name, expected_sha in WANDS_SOURCE_SHA256.items():
        path = raw_dir / name
        payload = path.read_bytes() if path.is_file() else _download(f"{WANDS_BASE_URL}/{name}")
        actual_sha = sha256_bytes(payload)
        if actual_sha != expected_sha:
            raise ValueError(f"WANDS {name} SHA mismatch: {actual_sha}")
        if not path.is_file():
            atomic_write_bytes(path, payload)
        result[name] = path
    return result


def _rows(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8")), delimiter="\t"))


def select_subset(
    query_path: Path,
    product_path: Path,
    label_path: Path,
    *,
    product_cap: int = 5_000,
    minimum_depth: int = 25,
    maximum_depth: int = 200,
) -> dict[str, Any]:
    """Select queries by new-class coverage, then minimum product-union growth."""

    query_rows = _rows(query_path)
    product_rows = _rows(product_path)
    product_by_id = {row["product_id"]: row for row in product_rows}
    labels_by_query: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in _rows(label_path):
        if row["label"] not in LABEL_GRADES:
            raise ValueError(f"unsupported WANDS label: {row['label']}")
        labels_by_query[row["query_id"]].append(row)

    eligible: list[dict[str, Any]] = []
    for query in query_rows:
        labels = labels_by_query.get(query["query_id"], [])
        product_ids = {row["product_id"] for row in labels}
        label_values = {row["label"] for row in labels}
        if not minimum_depth <= len(product_ids) <= maximum_depth:
            continue
        if not {"Exact", "Irrelevant"}.issubset(label_values):
            continue
        missing = sorted(product_ids - set(product_by_id))
        if missing:
            raise ValueError(f"WANDS query {query['query_id']} references missing products")
        eligible.append({**query, "labels": labels, "productIds": product_ids})

    selected: list[dict[str, Any]] = []
    product_union: set[str] = set()
    covered_classes: set[str] = set()
    remaining = list(eligible)
    while remaining:
        choices: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        for row in remaining:
            added = len(row["productIds"] - product_union)
            if len(product_union) + added > product_cap:
                continue
            new_class = int(row["query_class"] not in covered_classes)
            tie = hashlib.sha256(row["query_id"].encode("utf-8")).hexdigest()
            choices.append(((-new_class, added, tie), row))
        if not choices:
            break
        choices.sort(key=lambda item: item[0])
        chosen = choices[0][1]
        selected.append(chosen)
        product_union.update(chosen["productIds"])
        covered_classes.add(chosen["query_class"])
        remaining = [row for row in remaining if row["query_id"] != chosen["query_id"]]

    query_cases: list[dict[str, Any]] = []
    for row in selected:
        labels = {
            label["product_id"]: LABEL_GRADES[label["label"]]
            for label in row["labels"]
        }
        query_cases.append(
            {
                "id": f"wands-{row['query_id']}",
                "queryId": row["query_id"],
                "query": row["query"],
                "queryClass": row["query_class"],
                "split": "external",
                "labelScope": "judged-pool",
                "relevanceGrades": labels,
            }
        )
    products = [product_by_id[product_id] for product_id in sorted(product_union, key=int)]
    result = {
        "schemaVersion": 1,
        "source": "WANDS",
        "sourceCommit": WANDS_COMMIT,
        "sourceSha256": WANDS_SOURCE_SHA256,
        "selectionPolicy": {
            "requiresExactAndIrrelevant": True,
            "minimumJudgmentDepth": minimum_depth,
            "maximumJudgmentDepth": maximum_depth,
            "productCap": product_cap,
            "priority": ["new-query-class", "minimum-added-products", "sha256-query-id"],
        },
        "counts": {
            "eligibleQueries": len(eligible),
            "queries": len(query_cases),
            "products": len(products),
            "judgments": sum(len(row["labels"]) for row in selected),
            "queryClasses": len(covered_classes),
        },
        "queries": query_cases,
        "products": products,
    }
    validate_subset(result, product_cap=product_cap)
    return result


def validate_subset(payload: dict[str, Any], *, product_cap: int = 5_000) -> None:
    products = payload.get("products") or []
    queries = payload.get("queries") or []
    product_ids = {str(row.get("product_id") or "") for row in products}
    if not products or len(product_ids) != len(products) or "" in product_ids:
        raise ValueError("WANDS selected products must be non-empty and unique")
    if len(products) > product_cap:
        raise ValueError("WANDS selected products exceed the configured cap")
    query_ids = [str(row.get("queryId") or "") for row in queries]
    if not queries or "" in query_ids or len(query_ids) != len(set(query_ids)):
        raise ValueError("WANDS selected queries must be non-empty and unique")
    for query in queries:
        grades = query.get("relevanceGrades") or {}
        if set(grades) - product_ids:
            raise ValueError(f"WANDS {query.get('id')} contains missing products")
        if 2 not in grades.values() or 0 not in grades.values():
            raise ValueError(f"WANDS {query.get('id')} lacks Exact or Irrelevant labels")
        if query.get("labelScope") != "judged-pool":
            raise ValueError("WANDS label scope must be judged-pool")


def prepare_wands(raw_dir: Path, output_dir: Path) -> dict[str, Any]:
    sources = download_sources(raw_dir)
    subset = select_subset(
        sources["query.csv"],
        sources["product.csv"],
        sources["label.csv"],
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_dir / "selection.json", subset)
    lock = {
        "schemaVersion": 1,
        "source": "WANDS",
        "sourceCommit": WANDS_COMMIT,
        "sourceSha256": WANDS_SOURCE_SHA256,
        "selectionSha256": sha256_bytes(
            json.dumps(subset, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ),
        **subset["counts"],
        "labelScope": "judged-pool",
    }
    atomic_write_json(output_dir / "selection.lock.json", lock)
    return {"selection": subset, "lock": lock}
