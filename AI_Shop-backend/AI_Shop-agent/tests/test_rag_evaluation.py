from pathlib import Path

from app.rag.evaluation import evaluate_results, placeholder_references
from scripts.eval_rag import select_threshold_result, validate_local_contract


def test_evaluate_results_reports_recall_mrr_and_citation_rate():
    cases = [
        {"relevantIds": ["faq_1"], "answerKeywords": ["发票"]},
        {"relevantIds": ["chunk_2"], "answerKeywords": ["配送"]},
    ]
    results = [
        {
            "source_refs": [
                {"id": "faq_1", "snippet": "发票可在订单详情页申请。"},
            ]
        },
        {
            "source_refs": [
                {"id": "other", "snippet": "其他内容"},
                {"chunkId": "chunk_2", "snippet": "配送范围覆盖全国。"},
            ]
        },
    ]

    metrics = evaluate_results(cases, results, top_k=2)

    assert metrics["cases"] == 2
    assert metrics["retrievalCases"] == 2
    assert metrics["recallAtK"] == 1.0
    assert metrics["mrr"] == 0.75
    assert metrics["ndcgAtK"] == 0.8155
    assert metrics["topKHitRate"] == 1.0
    assert metrics["answerCitationRate"] == 1.0
    assert metrics["citationCorrectness"] == 0.6667
    assert metrics["labelCitationPrecision"] == 0.6667
    assert metrics["citationCoverage"] == 1.0
    assert len(metrics["perCase"]) == 2


def test_evaluate_results_uses_true_recall_and_excludes_no_answer_cases():
    cases = [
        {"relevantIds": ["a", "b"], "answerKeywords": ["规则"]},
        {"relevantIds": ["c"], "answerKeywords": ["配送"]},
        {"relevantIds": [], "answerKeywords": []},
    ]
    results = [
        {"source_refs": [{"id": "a", "snippet": "规则说明"}]},
        {"source_refs": [{"id": "other", "snippet": "其他"}]},
        {"source_refs": []},
    ]

    metrics = evaluate_results(cases, results, top_k=2)

    assert metrics["cases"] == 3
    assert metrics["retrievalCases"] == 2
    assert metrics["noAnswerCases"] == 1
    assert metrics["recallAtK"] == 0.25
    assert metrics["mrr"] == 0.5
    assert metrics["topKHitRate"] == 0.5
    assert metrics["answerCitationRate"] == 0.5
    assert metrics["noAnswerAccuracy"] == 1.0


def test_evaluate_results_handles_empty_dataset():
    metrics = evaluate_results([], [])
    assert metrics["cases"] == 0
    assert metrics["ndcgAtK"] == 0.0
    assert metrics["citationCorrectness"] == 0.0
    assert metrics["injectionRobustness"] == 0.0


def test_no_answer_accuracy_rejects_false_positive_citations():
    cases = [
        {"query": "不存在的规则", "relevantIds": [], "noAnswer": True},
        {"query": "另一个不存在的问题", "relevantIds": [], "noAnswer": True},
    ]
    results = [
        {"source_refs": [], "trace": {"hit": False}},
        {"source_refs": [{"id": "irrelevant"}], "trace": {"hit": True}},
    ]

    metrics = evaluate_results(cases, results, top_k=10)
    assert metrics["noAnswerAccuracy"] == 0.5
    assert metrics["noAnswerPrecision"] == 1.0
    assert metrics["noAnswerRecall"] == 0.5
    assert metrics["noAnswerF1"] == 0.6667


def test_stable_faq_and_knowledge_refs_match_runtime_metadata():
    cases = [
        {
            "query": "优惠券能叠加吗",
            "relevantRefs": [{"type": "faq", "questionId": "9002"}],
            "answerKeywords": ["一张"],
        },
        {
            "query": "地址错了",
            "relevantRefs": [
                {
                    "type": "knowledge",
                    "source": "02-orders-delivery-and-returns.md",
                    "heading": "配送说明",
                }
            ],
            "answerKeywords": ["地址"],
        },
    ]
    results = [
        {"source_refs": [{"id": "faq_9002", "questionId": 9002, "snippet": "只能使用一张"}]},
        {
            "source_refs": [
                {
                    "chunkId": "knowledge_7_5_1",
                    "source": "/upload/02-orders-delivery-and-returns.md",
                    "heading": "配送说明",
                    "snippet": "地址应在提交前确认",
                }
            ]
        },
    ]

    metrics = evaluate_results(cases, results, top_k=5)
    assert metrics["recallAtK"] == 1.0
    assert metrics["mrr"] == 1.0
    assert metrics["placeholderRefs"] == []


def test_placeholder_faq_ids_are_rejected():
    placeholders = placeholder_references(
        [{"query": "发票", "relevantIds": ["faq_invoice_apply"]}]
    )
    assert placeholders[0]["ref"] == "faq_invoice_apply"


def test_citation_correctness_coverage_and_injection_are_separate_metrics():
    cases = [
        {
            "query": "优惠券规则",
            "relevantIds": ["faq_1", "faq_2"],
            "answerKeywords": ["一张"],
            "injection": True,
        },
        {
            "query": "不存在的支付方式",
            "relevantIds": [],
            "noAnswer": True,
            "injection": True,
        },
    ]
    results = [
        {
            "source_refs": [
                {"id": "faq_1", "snippet": "一次只能使用一张券"},
                {"id": "unrelated", "snippet": "一张图片"},
            ],
            "trace": {"hit": True, "latencyMs": 12.5},
        },
        {"source_refs": [], "trace": {"hit": False, "latencyMs": 3.0}},
    ]

    metrics = evaluate_results(cases, results, top_k=2)

    assert metrics["citationCorrectness"] == 0.5
    assert metrics["citationCoverage"] == 0.5
    assert metrics["injectionCases"] == 2
    assert metrics["injectionRobustness"] == 0.5
    assert metrics["perCase"][0]["latencyMs"] == 12.5


def test_semantically_duplicate_published_evidence_counts_as_supported_but_not_label_match():
    cases = [
        {
            "relevantRefs": [{"type": "faq", "questionId": "9002"}],
            "answerKeywords": ["一张", "优惠券"],
        }
    ]
    results = [
        {
            "source_refs": [
                {
                    "type": "faq",
                    "questionId": "9002",
                    "snippet": "一个订单只能选择一张优惠券。",
                },
                {
                    "type": "knowledge_chunk",
                    "source": "03-membership-and-coupons.md",
                    "heading": "使用限制",
                    "snippet": "单笔订单只能选择一张用户优惠券。",
                },
            ]
        }
    ]

    metrics = evaluate_results(cases, results, top_k=2)

    assert metrics["citationCorrectness"] == 1.0
    assert metrics["labelCitationPrecision"] == 0.5
    assert metrics["citationCoverage"] == 1.0


def test_locked_rag_dataset_and_knowledge_files_match():
    contract = validate_local_contract(
        Path("scripts/rag_golden.jsonl"),
        Path("scripts/rag_golden.lock.json"),
    )
    assert contract["cases"] == 34
    assert contract["lock"]["selectedThreshold"] == 0.65
    assert contract["lock"]["qualityBaseline"]["noAnswerF1"] == 0.9524


def test_frozen_rag_threshold_cannot_be_reselected_to_hide_a_regression():
    scans = [
        {
            "threshold": 0.60,
            "recallAtK": 1.0,
            "mrr": 1.0,
            "noAnswerF1": 1.0,
            "answerCitationRate": 1.0,
        },
        {
            "threshold": 0.65,
            "recallAtK": 0.7,
            "mrr": 0.7,
            "noAnswerF1": 0.7,
            "answerCitationRate": 0.7,
        },
    ]

    selected = select_threshold_result(scans, {"selectedThreshold": 0.65})

    assert selected["threshold"] == 0.65
    assert selected["recallAtK"] == 0.7
