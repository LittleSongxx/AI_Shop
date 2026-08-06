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
