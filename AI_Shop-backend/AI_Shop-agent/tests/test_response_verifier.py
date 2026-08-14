from app.rag.prompt_builder import grounding_repair_reason
from app.services.response_verifier import response_verifier


def test_dynamic_order_fact_requires_a_tool_or_resolved_order_reference():
    blocked = response_verifier.verify(
        assistant="订单已发货",
        biz_type="query_order",
        tools_called=[],
        source_refs=None,
        has_pending_action=False,
    )
    grounded = response_verifier.verify(
        assistant="订单已发货",
        biz_type="query_order",
        tools_called=["QUERY_ORDERS"],
        source_refs=None,
        has_pending_action=False,
    )

    assert blocked.passed is False
    assert blocked.issues[0].code == "DYNAMIC_FACT_WITHOUT_TOOL"
    assert grounded.passed is True


def test_write_tool_requires_a_server_verified_pending_action():
    result = response_verifier.verify(
        assistant="请确认退款",
        biz_type="action_confirm",
        tools_called=["PROPOSE_REFUND"],
        source_refs=None,
        has_pending_action=False,
    )

    assert result.passed is False
    assert result.action == "HANDOFF"
    assert result.issues[0].code == "WRITE_WITHOUT_PENDING_ACTION"


def test_policy_claim_requires_published_source_reference():
    blocked = response_verifier.verify(
        assistant="平台规定七天内支持无理由退货",
        biz_type="agent",
        tools_called=[],
        source_refs=[],
        has_pending_action=False,
    )
    grounded = response_verifier.verify(
        assistant="平台规定七天内支持无理由退货",
        biz_type="agent",
        tools_called=[],
        source_refs={"sources": [{"documentId": "7", "version": 2}]},
        has_pending_action=False,
    )

    assert blocked.issues[0].code == "POLICY_WITHOUT_CITATION"
    assert grounded.passed is True


def test_policy_evidence_gate_rejects_uncited_answer_without_keyword_match():
    result = response_verifier.verify(
        assistant="这种情况可以办理。",
        biz_type="agent",
        tools_called=["SEARCH_KNOWLEDGE"],
        source_refs=[],
        has_pending_action=False,
        policy_evidence_required=True,
    )

    assert result.passed is False
    assert result.action == "DEGRADE"
    assert result.issues[0].code == "POLICY_WITHOUT_CITATION"


def test_policy_evidence_gate_preserves_grounded_facts_when_answer_abstains():
    assistant = (
        "订单 SM1 当前已发货，物流正在派送。"
        "未找到可引用的售后政策证据，因此无法确认是否符合退款条件。"
    )

    result = response_verifier.verify(
        assistant=assistant,
        biz_type="agent",
        tools_called=[],
        source_refs=[],
        has_pending_action=False,
        order_resolution="RESOLVED",
        policy_evidence_required=True,
    )

    assert result.passed is True
    assert result.action == "PASS"
    assert result.assistant == assistant


def test_policy_evidence_gate_rejects_uncited_eligibility_claim():
    result = response_verifier.verify(
        assistant="该订单符合退款条件，可以退款。",
        biz_type="agent",
        tools_called=[],
        source_refs=[],
        has_pending_action=False,
        order_resolution="RESOLVED",
        policy_evidence_required=True,
    )

    assert result.passed is False
    assert result.issues[0].code == "POLICY_WITHOUT_CITATION"


def test_policy_abstention_cannot_mask_an_uncited_eligibility_claim():
    result = response_verifier.verify(
        assistant="该订单可以退款，但我无法确认具体售后资格。",
        biz_type="agent",
        tools_called=[],
        source_refs=[],
        has_pending_action=False,
        order_resolution="RESOLVED",
        policy_evidence_required=True,
    )

    assert result.passed is False
    assert result.issues[0].code == "POLICY_WITHOUT_CITATION"


def test_policy_violation_uses_a_separately_verified_safe_fallback():
    fallback = (
        "订单 SM1 当前已发货，最新物流为派送中。"
        "未找到可引用的售后政策证据，因此无法确认具体售后资格。"
    )

    result = response_verifier.verify(
        assistant="该订单符合退款条件，可以退款。",
        biz_type="agent",
        tools_called=[],
        source_refs=[],
        has_pending_action=False,
        order_resolution="RESOLVED",
        policy_evidence_required=True,
        safe_fallback=fallback,
    )

    assert result.passed is False
    assert result.action == "DEGRADE"
    assert result.assistant == fallback


def test_policy_violation_rejects_an_unsafe_custom_fallback():
    result = response_verifier.verify(
        assistant="该订单符合退款条件。",
        biz_type="agent",
        tools_called=[],
        source_refs=[],
        has_pending_action=False,
        order_resolution="RESOLVED",
        policy_evidence_required=True,
        safe_fallback="可以退款。",
    )

    assert result.passed is False
    assert result.assistant.startswith("当前没有检索到足够的已发布规则依据")


def test_supported_rag_abstention_is_rejected():
    result = response_verifier.verify(
        assistant="根据当前知识库，我无法确认该信息。请联系人工客服核实。",
        biz_type="agent",
        tools_called=["SEARCH_KNOWLEDGE"],
        source_refs=[{"id": "knowledge_1", "source": "规则.md"}],
        has_pending_action=False,
        policy_evidence_required=True,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )

    assert result.passed is False
    assert result.issues[0].code == "UNNECESSARY_RAG_ABSTENTION"


def test_rag_citation_must_exist_and_stay_in_range():
    missing = response_verifier.verify(
        assistant="每个订单只能使用一张优惠券。",
        biz_type="agent",
        tools_called=["SEARCH_KNOWLEDGE"],
        source_refs=[{"id": "faq_9002"}],
        has_pending_action=False,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )
    invalid = response_verifier.verify(
        assistant="每个订单只能使用一张优惠券。[2]",
        biz_type="agent",
        tools_called=["SEARCH_KNOWLEDGE"],
        source_refs=[{"id": "faq_9002"}],
        has_pending_action=False,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )
    valid = response_verifier.verify(
        assistant="每个订单只能使用一张优惠券。[1]",
        biz_type="agent",
        tools_called=["SEARCH_KNOWLEDGE"],
        source_refs=[{"id": "faq_9002"}],
        has_pending_action=False,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )

    assert missing.issues[0].code == "INVALID_RAG_CITATION"
    assert invalid.issues[0].code == "INVALID_RAG_CITATION"
    assert valid.passed is True


def test_rag_citation_is_checked_per_factual_sentence():
    result = response_verifier.verify(
        assistant="每个订单只能使用一张优惠券。[1] 支付失败后优惠券会释放。",
        biz_type="agent",
        tools_called=["SEARCH_KNOWLEDGE"],
        source_refs=[{"id": "faq_9002"}],
        has_pending_action=False,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )

    assert result.passed is False
    assert result.issues[0].code == "INVALID_RAG_CITATION"
    assert "事实句" in result.issues[0].detail


def test_grounding_repair_catches_uncited_short_boundary_answer():
    reason = grounding_repair_reason(
        "不保证。加购价格快照可能变化 [1]。",
        evidence_state="SUPPORTED",
        evidence_count=1,
    )

    assert reason is not None
    assert "事实句缺少就近引用" in reason


def test_recommendation_hard_constraints_are_deterministic():
    result = response_verifier.verify(
        assistant="为你找到了两款",
        biz_type="product_search",
        tools_called=["SEARCH_PRODUCTS"],
        source_refs=None,
        has_pending_action=False,
        recommendation_constraints={
            "budgetMax": 1000,
            "requiredTerms": ["降噪"],
            "excludedBrands": ["Acme"],
        },
        recommendation_candidates=[
            {"name": "Acme 降噪耳机", "brand": "Acme", "price": 800}
        ],
    )

    assert result.passed is False
    assert result.action == "CLARIFY"


def test_recommendation_budget_range_and_required_brand_are_checked():
    result = response_verifier.verify(
        assistant="已找到候选",
        biz_type="product_search",
        tools_called=["SEARCH_PRODUCTS"],
        source_refs=None,
        has_pending_action=False,
        recommendation_constraints={
            "budgetMin": 3000,
            "budgetMax": 5000,
            "requiredBrands": ["华为"],
        },
        recommendation_candidates=[
            {
                "productName": "其他品牌手机",
                "brand": "其他",
                "minPrice": 2000,
                "maxPrice": 2500,
            }
        ],
    )

    assert result.passed is False
    assert result.issues[0].code == "RECOMMENDATION_CONSTRAINT_VIOLATION"
