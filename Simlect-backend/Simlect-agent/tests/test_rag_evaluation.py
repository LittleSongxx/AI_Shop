from app.rag.evaluation import evaluate_results


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

    assert metrics == {
        "cases": 2,
        "retrievalCases": 2,
        "recallAtK": 1.0,
        "mrr": 0.75,
        "topKHitRate": 1.0,
        "answerCitationRate": 1.0,
    }


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

    assert metrics == {
        "cases": 3,
        "retrievalCases": 2,
        "recallAtK": 0.25,
        "mrr": 0.5,
        "topKHitRate": 0.5,
        "answerCitationRate": 0.5,
    }


def test_evaluate_results_handles_empty_dataset():
    assert evaluate_results([], [])["cases"] == 0
