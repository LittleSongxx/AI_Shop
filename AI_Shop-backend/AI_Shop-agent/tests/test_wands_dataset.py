import csv
import json
from pathlib import Path

import pytest

from app.evaluation.ranking import incomplete_judgment_case_metrics
from benchmarks.mature_eval.wands import (
    LABEL_GRADES,
    WANDS_COMMIT,
    WANDS_SOURCE_SHA256,
    select_subset,
    validate_subset,
)


def _write_tsv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path: Path):
    query_path = tmp_path / "query.csv"
    product_path = tmp_path / "product.csv"
    label_path = tmp_path / "label.csv"
    _write_tsv(
        query_path,
        ["query_id", "query", "query_class"],
        [
            {"query_id": "1", "query": "chair", "query_class": "chairs"},
            {"query_id": "2", "query": "desk", "query_class": "desks"},
        ],
    )
    _write_tsv(
        product_path,
        [
            "product_id",
            "product_name",
            "product_class",
            "category hierarchy",
            "product_description",
            "product_features",
            "rating_count",
            "average_rating",
            "review_count",
        ],
        [
            {
                "product_id": str(index),
                "product_name": f"product {index}",
                "product_class": "fixture",
                "category hierarchy": "fixture",
                "product_description": "fixture",
                "product_features": "",
                "rating_count": "0",
                "average_rating": "0",
                "review_count": "0",
            }
            for index in range(1, 7)
        ],
    )
    labels = []
    label_id = 1
    for query_id, product_ids in (("1", ["1", "2", "3"]), ("2", ["3", "4", "5"])):
        for offset, product_id in enumerate(product_ids):
            labels.append(
                {
                    "id": str(label_id),
                    "query_id": query_id,
                    "product_id": product_id,
                    "label": "Exact" if offset == 0 else "Irrelevant",
                }
            )
            label_id += 1
    _write_tsv(label_path, ["id", "query_id", "product_id", "label"], labels)
    return query_path, product_path, label_path


def test_wands_source_contract_is_fixed():
    assert WANDS_COMMIT == "3b74dcf4ba29ab8ff3e6a50b5b09fc627cb882b5"
    assert set(WANDS_SOURCE_SHA256) == {"query.csv", "product.csv", "label.csv"}
    assert LABEL_GRADES == {"Exact": 2, "Partial": 1, "Irrelevant": 0}


def test_selector_is_stable_and_keeps_complete_judged_pools(tmp_path):
    paths = _fixture(tmp_path)
    first = select_subset(*paths, product_cap=5, minimum_depth=3, maximum_depth=3)
    second = select_subset(*paths, product_cap=5, minimum_depth=3, maximum_depth=3)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["counts"] == {
        "eligibleQueries": 2,
        "queries": 2,
        "products": 5,
        "judgments": 6,
        "queryClasses": 2,
    }
    assert all(len(case["relevanceGrades"]) == 3 for case in first["queries"])
    assert all(case["labelScope"] == "judged-pool" for case in first["queries"])


def test_selector_fails_when_a_judged_product_is_missing(tmp_path):
    query_path, product_path, label_path = _fixture(tmp_path)
    products = product_path.read_text(encoding="utf-8").splitlines()
    product_path.write_text("\n".join(products[:-1]) + "\n", encoding="utf-8")
    labels = label_path.read_text(encoding="utf-8").replace("\t5\tIrrelevant", "\t6\tIrrelevant")
    label_path.write_text(labels, encoding="utf-8")

    with pytest.raises(ValueError, match="missing products"):
        select_subset(
            query_path,
            product_path,
            label_path,
            product_cap=5,
            minimum_depth=3,
            maximum_depth=3,
        )


def test_validate_subset_does_not_allow_unlabelled_catalog_claims(tmp_path):
    paths = _fixture(tmp_path)
    payload = select_subset(*paths, product_cap=5, minimum_depth=3, maximum_depth=3)
    payload["queries"][0]["labelScope"] = "full-catalog"

    with pytest.raises(ValueError, match="judged-pool"):
        validate_subset(payload, product_cap=5)


def test_full_catalog_selector_excludes_conflicting_pairs_without_guessing(tmp_path):
    query_path, product_path, label_path = _fixture(tmp_path)
    with label_path.open("a", encoding="utf-8") as stream:
        stream.write("7\t1\t2\tExact\n")

    payload = select_subset(
        query_path,
        product_path,
        label_path,
        product_cap=6,
        minimum_depth=2,
        maximum_depth=3,
        include_all_products=True,
        exclude_conflicting_pairs=True,
        label_scope="full-catalog-incomplete-qrels",
    )

    case = next(row for row in payload["queries"] if row["queryId"] == "1")
    assert "2" not in case["relevanceGrades"]
    assert payload["counts"]["products"] == 6
    assert payload["judgmentAudit"]["ambiguousPairsExcluded"] == 1
    validate_subset(
        payload,
        product_cap=6,
        expected_label_scope="full-catalog-incomplete-qrels",
    )


def test_full_catalog_metrics_do_not_treat_unjudged_results_as_negative():
    metrics = incomplete_judgment_case_metrics(
        ["unjudged-a", "relevant", "irrelevant", "unjudged-b"],
        {"relevant": 2, "irrelevant": 0},
        k_values=(1, 2, 4),
    )

    assert metrics["metricsByK"]["1"]["judgedRate"] == 0.0
    assert metrics["metricsByK"]["2"]["knownRelevantRecall"] == 1.0
    assert metrics["metricsByK"]["4"]["unjudgedCount"] == 2
    assert metrics["bpref"] == 1.0
