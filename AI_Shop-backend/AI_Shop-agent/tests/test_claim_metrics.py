from app.rag.claim_metrics import required_claim_metrics


def test_claim_metrics_preserves_original_citation_numbers():
    case = {
        "relevantFactIds": ["coupon.single_per_order_and_revalidate"],
        "requiredClaims": [
            {
                "claimId": "c1",
                "factIds": ["coupon.single_per_order_and_revalidate"],
                "aliases": ["一张"],
                "required": True,
            }
        ],
    }
    refs = [
        {"type": "knowledge_chunk", "source": "无关.md", "heading": "其他"},
        {"type": "faq", "questionId": 9002},
    ]
    metrics = required_claim_metrics(case, "一个订单只能使用一张优惠券。[2]", refs)
    assert metrics["requiredClaimCompleteness"] == 1.0
    assert metrics["claimCitationSupport"] == 1.0
    assert metrics["invalidCitationCount"] == 0


def test_claim_metrics_detects_uncited_factual_sentence():
    case = {
        "relevantFactIds": ["coupon.single_per_order_and_revalidate"],
        "requiredClaims": [
            {
                "claimId": "c1",
                "factIds": ["coupon.single_per_order_and_revalidate"],
                "aliases": ["一张"],
                "required": True,
            }
        ],
    }
    refs = [{"type": "faq", "questionId": 9002}]
    metrics = required_claim_metrics(case, "一个订单只能使用一张优惠券。", refs)
    assert metrics["requiredClaimCompleteness"] == 1.0
    assert metrics["claimCitationSupport"] == 0.0
    assert metrics["unmappedFactualClaimRate"] == 1.0


def test_claim_metrics_scores_cited_markdown_list_lines_as_sentences():
    case = {
        "relevantFactIds": ["coupon.single_per_order_and_revalidate"],
        "requiredClaims": [
            {
                "claimId": "limit",
                "factIds": ["coupon.single_per_order_and_revalidate"],
                "aliases": ["每单"],
                "required": True,
            },
            {
                "claimId": "revalidate",
                "factIds": ["coupon.single_per_order_and_revalidate"],
                "aliases": ["重新校验"],
                "required": True,
            },
        ]
    }
    refs = [{"type": "faq", "questionId": 9002}]

    metrics = required_claim_metrics(
        case,
        "- **每单限制**：只能使用一张优惠券 [1]\n"
        "- **结算规则**：提交订单前重新校验 [1]",
        refs,
    )

    assert metrics["requiredClaimCompleteness"] == 1.0
    assert metrics["claimCitationSupport"] == 1.0
    assert metrics["unmappedFactualClaimRate"] == 0.0


def test_claim_metrics_uses_fact_scoped_controlled_equivalents():
    case = {
        "relevantFactIds": ["privacy.no_external_chat_import"],
        "requiredClaims": [
            {
                "claimId": "unsupported",
                "factIds": ["privacy.no_external_chat_import"],
                "aliases": ["不支持"],
                "required": True,
            },
            {
                "claimId": "memory",
                "factIds": ["privacy.no_external_chat_import"],
                "aliases": ["永久记忆"],
                "required": True,
            },
        ],
    }
    refs = [
        {
            "type": "knowledge_chunk",
            "source": "12-privacy-data-and-ai-boundaries.md",
            "heading": "不支持的外部数据导入",
        }
    ]

    metrics = required_claim_metrics(
        case,
        "平台不会自动导入微信聊天记录作为永久购物记忆 [1]。",
        refs,
    )

    assert metrics["requiredClaimCompleteness"] == 1.0
    assert metrics["claimCitationSupport"] == 1.0


def test_claim_metrics_does_not_apply_equivalent_across_fact_ids():
    case = {
        "relevantFactIds": ["payment.supported_channels"],
        "requiredClaims": [
            {
                "claimId": "unsupported",
                "factIds": ["payment.supported_channels"],
                "aliases": ["不支持"],
                "required": True,
            }
        ],
    }
    refs = [
        {
            "type": "knowledge_chunk",
            "source": "06-payment-and-refund-progress.md",
            "heading": "支持的支付方式",
        }
    ]

    metrics = required_claim_metrics(case, "平台不会自动发起支付 [1]。", refs)

    assert metrics["requiredClaimCompleteness"] == 0.0


def test_claim_metrics_prefers_cited_equivalent_over_uncited_literal_alias():
    case = {
        "relevantFactIds": ["privacy.no_external_chat_import"],
        "requiredClaims": [
            {
                "claimId": "unsupported",
                "factIds": ["privacy.no_external_chat_import"],
                "aliases": ["不支持"],
                "required": True,
            }
        ],
    }
    refs = [
        {
            "type": "knowledge_chunk",
            "source": "12-privacy-data-and-ai-boundaries.md",
            "heading": "不支持的外部数据导入",
        }
    ]

    metrics = required_claim_metrics(
        case,
        "不支持。平台不会自动导入外部聊天记录 [1]。",
        refs,
    )

    assert metrics["claims"][0]["matchedAlias"] == "不会自动"
    assert metrics["claimCitationSupport"] == 1.0


def test_claim_metrics_handles_controlled_negation_word_order():
    case = {
        "relevantFactIds": ["payment.demo_no_real_funds"],
        "requiredClaims": [
            {
                "claimId": "no-real-funds",
                "factIds": ["payment.demo_no_real_funds"],
                "aliases": ["不执行"],
                "required": True,
            }
        ],
    }
    refs = [
        {
            "type": "knowledge_chunk",
            "source": "06-payment-and-refund-progress.md",
            "heading": "演示环境边界",
        }
    ]

    metrics = required_claim_metrics(
        case,
        "演示环境不会执行真实资金交易 [1]。",
        refs,
    )

    assert metrics["requiredClaimCompleteness"] == 1.0
    assert metrics["claimCitationSupport"] == 1.0
