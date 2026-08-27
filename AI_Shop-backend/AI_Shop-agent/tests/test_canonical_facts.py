from app.rag.canonical_facts import (
    DEFAULT_CATALOG_PATH,
    LEGACY_V1_CATALOG_PATH,
    canonical_citation_metrics,
    canonical_fact_catalog_scope,
    concept_coverage,
    get_canonical_fact_catalog,
    normalize_concept_text,
    reference_key,
)
from app.rag.fact_metadata import get_fact_metadata_catalog


def test_runtime_uses_v3_overlay_while_legacy_scope_keeps_v1_catalog_and_metadata():
    runtime = get_canonical_fact_catalog()

    assert runtime.catalog_version == 3
    assert runtime.path.resolve() == DEFAULT_CATALOG_PATH.resolve()
    assert get_fact_metadata_catalog().path.name == "fact-metadata.v3.json"
    assert "payment.safe_retry_guidance" in runtime.fact_to_refs
    assert "product.display_technology_boundary" in runtime.fact_to_refs

    with canonical_fact_catalog_scope(LEGACY_V1_CATALOG_PATH) as legacy:
        assert legacy.catalog_version == 1
        assert get_canonical_fact_catalog() is legacy
        assert get_fact_metadata_catalog().path.name == "fact-metadata.v1.json"

    assert get_canonical_fact_catalog() is runtime


def test_v3_overlay_removes_topic_only_refund_equivalence():
    catalog = get_canonical_fact_catalog()

    facts = catalog.facts_for_ref(
        {
            "type": "knowledge_chunk",
            "source": "02-orders-delivery-and-returns.md",
            "heading": "退货与退款",
        }
    )

    assert facts == frozenset({"aftersales.request_and_refund_boundary"})


def test_catalog_maps_faq_and_markdown_equivalent_evidence_to_same_fact():
    catalog = get_canonical_fact_catalog()

    faq = catalog.facts_for_ref({"type": "faq", "questionId": "9002"})
    markdown = catalog.facts_for_ref(
        {
            "type": "knowledge_chunk",
            "source": "10-promotions-and-coupon-rush.md",
            "heading": "单订单使用限制",
        }
    )

    assert "coupon.single_per_order_and_revalidate" in faq
    assert "coupon.single_per_order_and_revalidate" in markdown
    assert reference_key("faq:9002") == "faq:9002"


def test_canonical_metrics_accept_equivalent_source_but_reject_topic_only_source():
    case = {
        "relevantFactIds": ["coupon.single_per_order_and_revalidate"],
    }
    refs = [
        {
            "type": "knowledge_chunk",
            "source": "10-promotions-and-coupon-rush.md",
            "heading": "单订单使用限制",
        },
        {
            "type": "knowledge_chunk",
            "source": "10-promotions-and-coupon-rush.md",
            "heading": "优惠券类型",
        },
    ]

    metrics = canonical_citation_metrics(case, refs)

    assert metrics["correctness"] == 0.5
    assert metrics["coverage"] == 1.0
    assert metrics["missingFactIds"] == []


def test_concept_coverage_normalizes_chinese_numbers_spacing_and_controlled_aliases():
    case = {
        "requiredConcepts": [
            {"aliases": ["一张", "1张"]},
            {"aliases": ["优惠券", "券"]},
            {"aliases": ["七天", "7天"]},
        ]
    }

    result = concept_coverage(case, "每单只能用 1 张券，连续 7 天可得奖励。")

    assert result["coverage"] == 1.0
    assert normalize_concept_text("一 张") == "1张"


def test_v3_case_contract_rejects_unknown_fact_and_inconsistent_refusal():
    catalog = get_canonical_fact_catalog()
    errors = catalog.validate_case(
        {
            "id": "bad",
            "expectedBehavior": "ANSWER",
            "noAnswer": True,
            "relevantFactIds": ["missing.fact"],
            "requiredConcepts": [{"aliases": []}],
        }
    )

    assert any("unknown relevantFactIds" in error for error in errors)
    assert any("noAnswer must match" in error for error in errors)
    assert any("has no aliases" in error for error in errors)
