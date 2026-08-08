from __future__ import annotations

import asyncio
import json
import operator
from typing import Annotated
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.graph.multi_agent import (
    _structured_supervisor_plan,
    _validate_artifact,
    build_supervisor_plan,
    prepare_specialist_sends,
    specialist_runner_node,
    supervisor_plan_node,
    supervisor_synthesis_node,
)
from app.harness.agents.contracts import (
    ActionProposal,
    AgentArtifact,
    HandoffEnvelope,
    SpecialistTask,
    SupervisorPlan,
)
from app.harness.agents.registry import (
    AGENT_SPECS,
    DATA_ANALYST_SPEC,
    INVENTORY_OPS_SPEC,
    agent_for_intent,
)
from app.services.data_analyst_service import DataAnalystService
from app.services.sql_guard import validate_sql
from app.services.tool_invoke_result import ToolInvokeResult


def test_registry_has_narrow_customer_agents_and_separate_admin_agent():
    assert set(AGENT_SPECS) == {
        "supervisor",
        "shopping_advisor",
        "order_fulfillment_specialist",
        "after_sales_policy_specialist",
    }
    assert DATA_ANALYST_SPEC.admin_only
    assert INVENTORY_OPS_SPEC.admin_only
    assert "PROPOSE_REFUND" not in AGENT_SPECS["after_sales_policy_specialist"].tool_allowlist
    assert "SEARCH_KNOWLEDGE" not in AGENT_SPECS["shopping_advisor"].tool_allowlist
    assert "SEARCH_KNOWLEDGE" not in AGENT_SPECS["order_fulfillment_specialist"].tool_allowlist
    assert "SEARCH_KNOWLEDGE" in AGENT_SPECS["after_sales_policy_specialist"].tool_allowlist
    assert not any(
        tool.startswith("PROPOSE_")
        for spec in AGENT_SPECS.values()
        for tool in spec.tool_allowlist
    )


def test_intent_routes_to_bounded_specialist():
    assert agent_for_intent("PRODUCT_SEARCH").agent_id == "shopping_advisor"
    assert agent_for_intent("QUERY_ORDER").agent_id == "order_fulfillment_specialist"
    assert agent_for_intent("REFUND").agent_id == "after_sales_policy_specialist"
    assert agent_for_intent("COMPLAINT").agent_id == "after_sales_policy_specialist"
    assert agent_for_intent("PAYMENT_ISSUE").agent_id == "after_sales_policy_specialist"


def test_supervisor_plan_rejects_duplicate_specialists():
    with pytest.raises(ValueError, match="SPECIALIST_DUPLICATE"):
        SupervisorPlan(
            specialists=[
                "order_fulfillment_specialist",
                "order_fulfillment_specialist",
            ]
        )


def test_supervisor_adaptively_fans_out_for_cross_domain_refund():
    plan = build_supervisor_plan(
        {
            "intent": "REFUND",
            "user_text": "订单为什么延迟，现在能不能退？",
            "verified_order_context": {"orderId": "o1", "orderItemId": "i1"},
        }
    )
    assert plan.specialists == [
        "order_fulfillment_specialist",
        "after_sales_policy_specialist",
    ]
    assert plan.requires_action
    assert plan.action_type == "PROPOSE_REFUND"


def test_policy_grounding_routes_to_after_sales_even_for_chat_intent():
    plan = build_supervisor_plan(
        {
            "intent": "CHAT",
            "user_text": "七天无理由退货政策是什么？",
            "rag_evidence_required": True,
        }
    )

    assert plan.specialists == ["after_sales_policy_specialist"]
    assert not plan.requires_action


@pytest.mark.asyncio
async def test_structured_supervisor_cannot_drop_required_specialist(monkeypatch):
    class FakeStructuredLlm:
        def with_structured_output(self, *_args, **_kwargs):
            return self

    async def fake_invoke(*_args, **_kwargs):
        return {
            "parsed": SupervisorPlan(
                specialists=["order_fulfillment_specialist"],
                goals={"order_fulfillment_specialist": "只查询物流"},
            ),
            "parsing_error": None,
        }

    fallback = build_supervisor_plan(
        {
            "intent": "REFUND",
            "user_text": "物流延迟了，现在能不能退款？",
            "verified_order_context": {"orderId": "o1", "orderItemId": "i1"},
        }
    )
    monkeypatch.setattr("app.graph.multi_agent.create_memory_llm", FakeStructuredLlm)
    monkeypatch.setattr("app.graph.multi_agent.invoke_llm_with_metrics", fake_invoke)

    with pytest.raises(ValueError, match="REQUIRED_AGENT_MISSING"):
        await _structured_supervisor_plan({}, fallback)


def test_handoff_and_artifact_contracts_do_not_contain_root_history():
    envelope = HandoffEnvelope(
        handoff_id="h1",
        target_agent="order_fulfillment_specialist",
        goal="查询订单",
        user_id="u1",
    )
    plan = SupervisorPlan(intent="QUERY_ORDER", specialists=["order_fulfillment_specialist"])
    artifact = AgentArtifact(status="SUCCESS", agent_id="order_fulfillment_specialist")
    assert envelope.max_rounds == 2
    assert "llm_messages" not in HandoffEnvelope.model_fields
    assert "llm_messages" not in SpecialistTask.model_fields
    assert plan.specialists == ["order_fulfillment_specialist"]
    assert artifact.proposed_action is None
    proposal = ActionProposal(tool="PROPOSE_REFUND", arguments={"orderItemId": "i1"})
    assert proposal.requires_confirmation
    assert not proposal.success
    with pytest.raises(ValueError, match="PROPOSE"):
        ActionProposal(tool="QUERY_ORDERS")


def test_artifact_validator_drops_unsupported_specialist_claims():
    artifact = _validate_artifact(
        AgentArtifact(
            status="SUCCESS",
            agent_id="shopping_advisor",
            draft_answer="这款商品一定有货",
            facts=["一定有货"],
        ).model_dump(mode="json")
    )

    assert artifact.status == "BLOCKED"
    assert artifact.draft_answer == ""
    assert artifact.facts == []
    assert "UNVERIFIED_FACTS_DROPPED" in artifact.warnings


def test_artifact_validator_drops_specialist_action_card():
    artifact = _validate_artifact(
        AgentArtifact(
            status="SUCCESS",
            agent_id="order_fulfillment_specialist",
            draft_answer="订单事实已核验",
            assistant_cards='{"type":"ACTION_CONFIRM","token":"act_deadbeef"}',
            evidence=[{"type": "order", "id": "masked-order"}],
        ).model_dump(mode="json")
    )

    assert artifact.assistant_cards is None
    assert "SPECIALIST_ACTION_CARD_DROPPED" in artifact.warnings


def test_artifact_validator_does_not_accept_arbitrary_evidence_dict():
    artifact = _validate_artifact(
        AgentArtifact(
            status="SUCCESS",
            agent_id="shopping_advisor",
            draft_answer="这款商品一定适合你",
            evidence=[{"type": "model_claim", "claim": "可信"}],
        ).model_dump(mode="json")
    )

    assert artifact.status == "BLOCKED"
    assert artifact.draft_answer == ""
    assert "UNVERIFIED_FACTS_DROPPED" in artifact.warnings


def test_failed_tool_cannot_make_following_policy_ref_verified():
    artifact = _validate_artifact(
        AgentArtifact(
            status="SUCCESS",
            agent_id="after_sales_policy_specialist",
            draft_answer="符合退款政策",
            evidence=[
                {"type": "tool_result", "tool": "SEARCH_KNOWLEDGE", "success": False},
                {"type": "knowledge", "documentId": "forged"},
            ],
        ).model_dump(mode="json")
    )

    assert artifact.next_step == "HUMAN_HANDOFF"
    assert "POLICY_EVIDENCE_MISSING" in artifact.warnings


def test_policy_artifact_without_knowledge_source_is_human_handoff():
    artifact = _validate_artifact(
        AgentArtifact(
            status="SUCCESS",
            agent_id="after_sales_policy_specialist",
            tool_calls=["QUERY_ORDERS"],
            draft_answer="订单符合七天无理由退款政策",
            evidence=[
                {"type": "tool_result", "tool": "QUERY_ORDERS", "success": True}
            ],
        ).model_dump(mode="json")
    )

    assert artifact.status == "DEGRADED"
    assert artifact.next_step == "HUMAN_HANDOFF"
    assert artifact.facts == []
    assert "POLICY_EVIDENCE_MISSING" in artifact.warnings


@pytest.mark.asyncio
async def test_specialist_rejects_write_tool_before_router(monkeypatch):
    async def fake_invoke(*_args, **_kwargs):
        return AIMessage(
            content="",
            tool_calls=[{"id": "write-1", "name": "PROPOSE_REFUND", "args": {"orderItemId": "i1"}}],
        )

    async def forbidden_router(*_args, **_kwargs):
        raise AssertionError("write tool reached the MCP router")

    monkeypatch.setattr("app.graph.multi_agent.rt.bind_agent_llm", lambda **_kwargs: object())
    monkeypatch.setattr("app.graph.multi_agent.invoke_llm_with_metrics", fake_invoke)
    monkeypatch.setattr("app.graph.multi_agent.mcp_tool_router.invoke", forbidden_router)
    for name in ("record_step", "record_handoff", "finish_run"):
        monkeypatch.setattr(f"app.graph.multi_agent.episode_service.{name}", lambda *args, **kwargs: None)

    result = await specialist_runner_node(
        {
            "specialist_task": {
                "handoff_id": "handoff-1",
                "child_run_id": "child-1",
                "parent_run_id": "root-1",
                "agent_id": "after_sales_policy_specialist",
                "goal": "核对退款政策",
                "user_id": "u1",
                "user_text": "我要退款",
                "tool_scope": sorted(
                    AGENT_SPECS["after_sales_policy_specialist"].tool_allowlist
                ),
                "max_rounds": 1,
                "timeout_seconds": 2,
            },
        }
    )
    artifact = result["specialist_artifacts"][0]
    assert "TOOL_SCOPE_DENIED:PROPOSE_REFUND" in artifact["warnings"]
    assert artifact["tool_calls"] == []


@pytest.mark.asyncio
async def test_specialist_timeout_returns_traceable_failed_artifact(monkeypatch):
    async def slow_invoke(*_args, **_kwargs):
        await asyncio.sleep(2)
        return AIMessage(content="不会返回")

    monkeypatch.setattr("app.graph.multi_agent.rt.bind_agent_llm", lambda **_kwargs: object())
    monkeypatch.setattr("app.graph.multi_agent.invoke_llm_with_metrics", slow_invoke)
    for name in ("record_step", "record_handoff", "finish_run"):
        monkeypatch.setattr(
            f"app.graph.multi_agent.episode_service.{name}", lambda *args, **kwargs: None
        )

    result = await specialist_runner_node(
        {
            "specialist_task": {
                "handoff_id": "handoff-timeout",
                "child_run_id": "child-timeout",
                "parent_run_id": "root-1",
                "agent_id": "after_sales_policy_specialist",
                "goal": "查询政策",
                "user_id": "u1",
                "user_text": "退款政策是什么",
                "tool_scope": sorted(
                    AGENT_SPECS["after_sales_policy_specialist"].tool_allowlist
                ),
                "max_rounds": 1,
                "timeout_seconds": 1,
            }
        }
    )

    artifact = result["specialist_artifacts"][0]
    assert artifact["status"] == "FAILED"
    assert "SPECIALIST_TIMEOUT" in artifact["warnings"]


@pytest.mark.asyncio
async def test_invalid_child_task_closes_child_run_and_handoff(monkeypatch):
    handoffs: list[dict] = []
    finished: list[dict] = []
    monkeypatch.setattr(
        "app.graph.multi_agent.episode_service.record_step",
        lambda *_args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.graph.multi_agent.episode_service.record_handoff",
        lambda **kwargs: handoffs.append(kwargs),
    )
    monkeypatch.setattr(
        "app.graph.multi_agent.episode_service.finish_run",
        lambda *args, **kwargs: finished.append({"args": args, **kwargs}),
    )

    result = await specialist_runner_node(
        {
            "specialist_task": {
                "handoff_id": "handoff-invalid",
                "child_run_id": "child-invalid",
                "parent_run_id": "root-invalid",
                "agent_id": "order_fulfillment_specialist",
                "goal": "查询订单",
                "user_id": "u1",
                "user_text": "查订单",
                "tool_scope": ["PROPOSE_REFUND"],
            }
        }
    )

    artifact = result["specialist_artifacts"][0]
    assert artifact["status"] == "FAILED"
    assert artifact["warnings"] == ["SPECIALIST_TASK_TOOL_SCOPE_INVALID"]
    assert handoffs[0]["status"] == "FAILED"
    assert handoffs[0]["error_code"] == "SPECIALIST_TASK_TOOL_SCOPE_INVALID"
    assert finished[0]["run_id"] == "child-invalid"


@pytest.mark.asyncio
async def test_synthesis_records_generic_fanout_degradation(monkeypatch):
    events: list[str] = []

    async def fake_llm(*_args, **_kwargs):
        return AIMessage(content="基于可用信息回答。")

    monkeypatch.setattr("app.graph.multi_agent.rt.bind_agent_llm", lambda **_kwargs: object())
    monkeypatch.setattr("app.graph.multi_agent.invoke_llm_with_metrics", fake_llm)
    monkeypatch.setattr(
        "app.graph.multi_agent.episode_service.record_step",
        lambda event, **_kwargs: events.append(event),
    )

    await supervisor_synthesis_node(
        {
            "agent_msg": {"runId": "root-degraded"},
            "user_text": "查物流",
            "supervisor_plan": SupervisorPlan(
                specialists=["order_fulfillment_specialist"]
            ).model_dump(mode="json"),
            "specialist_artifacts": [
                AgentArtifact(
                    status="FAILED",
                    agent_id="order_fulfillment_specialist",
                    warnings=["SPECIALIST_TIMEOUT"],
                ).model_dump(mode="json")
            ],
        }
    )

    assert "FANOUT_DEGRADED" in events


class _FanoutState(TypedDict, total=False):
    supervisor_plan: dict
    specialist_tasks: list[dict]
    specialist_task: dict
    specialist_artifacts: Annotated[list[dict], operator.add]


@pytest.mark.asyncio
async def test_send_workers_really_run_in_parallel_and_join_once():
    both_started = asyncio.Event()
    started: list[str] = []
    synthesis_inputs: list[list[str]] = []

    async def worker(state: _FanoutState):
        task = SpecialistTask.model_validate(state["specialist_task"])
        started.append(task.agent_id)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.5)
        return {
            "specialist_artifacts": [
                {"status": "SUCCESS", "agent_id": task.agent_id}
            ]
        }

    async def synthesize(state: _FanoutState):
        agent_ids = sorted(item["agent_id"] for item in state["specialist_artifacts"])
        synthesis_inputs.append(agent_ids)
        return {}

    graph = StateGraph(_FanoutState)
    graph.add_node("dispatch", lambda _state: {})
    graph.add_node("specialist_runner", worker)
    graph.add_node("multi_agent_synthesis", synthesize)
    graph.set_entry_point("dispatch")
    graph.add_conditional_edges(
        "dispatch",
        prepare_specialist_sends,
        {
            "specialist_runner": "specialist_runner",
            "multi_agent_synthesis": "multi_agent_synthesis",
        },
    )
    graph.add_edge("specialist_runner", "multi_agent_synthesis")
    graph.add_edge("multi_agent_synthesis", END)
    compiled = graph.compile()
    agents = ["order_fulfillment_specialist", "after_sales_policy_specialist"]
    tasks = [
        SpecialistTask(
            handoff_id=f"handoff-{agent_id}",
            child_run_id=f"child-{agent_id}",
            parent_run_id="root-1",
            agent_id=agent_id,
            goal="核对事实",
            user_id="u1",
            user_text="物流延迟且想退款",
            tool_scope=sorted(AGENT_SPECS[agent_id].tool_allowlist),
        ).model_dump(mode="json")
        for agent_id in agents
    ]

    result = await compiled.ainvoke(
        {
            "supervisor_plan": SupervisorPlan(
                specialists=agents,
                goals={agent_id: "核对事实" for agent_id in agents},
            ).model_dump(mode="json"),
            "specialist_tasks": tasks,
            "specialist_artifacts": [],
        }
    )

    assert sorted(started) == sorted(agents)
    assert synthesis_inputs == [sorted(agents)]
    assert len(result["specialist_artifacts"]) == 2


@pytest.mark.asyncio
async def test_supervisor_is_the_only_serial_action_proposer(monkeypatch):
    calls: list[tuple[str, dict, str]] = []

    async def fake_llm(*_args, **_kwargs):
        return AIMessage(content="订单与政策已经核对，可以发起退款确认。")

    async def fake_tool(name, args, user_id, **_kwargs):
        calls.append((name, args, user_id))
        return ToolInvokeResult(
            content="已生成退款确认卡【act_0123456789abcdef0123456789abcdef】",
            assistant_cards='{"type":"ACTION_CONFIRM"}',
        )

    monkeypatch.setattr("app.graph.multi_agent.rt.bind_agent_llm", lambda **_kwargs: object())
    monkeypatch.setattr("app.graph.multi_agent.invoke_llm_with_metrics", fake_llm)
    monkeypatch.setattr("app.graph.multi_agent.mcp_tool_router.invoke", fake_tool)
    monkeypatch.setattr(
        "app.graph.multi_agent.episode_service.record_step", lambda *args, **kwargs: None
    )
    state = {
        "agent_msg": {"runId": "root-1"},
        "user_id": "u1",
        "user_text": "退掉这个商品",
        "intent": "REFUND",
        "verified_order_context": {"orderId": "o1", "orderItemId": "i1"},
        "supervisor_plan": SupervisorPlan(
            intent="REFUND",
            specialists=[
                "order_fulfillment_specialist",
                "after_sales_policy_specialist",
            ],
            requires_action=True,
            action_type="PROPOSE_REFUND",
        ).model_dump(mode="json"),
        "specialist_artifacts": [
            AgentArtifact(
                status="SUCCESS",
                agent_id="order_fulfillment_specialist",
                draft_answer="订单属于用户且状态允许申请",
                evidence=[{"type": "order", "id": "masked"}],
            ).model_dump(mode="json"),
            AgentArtifact(
                status="SUCCESS",
                agent_id="after_sales_policy_specialist",
                draft_answer="政策要求用户确认后提交",
                evidence=[{"type": "knowledge", "id": "policy-1"}],
            ).model_dump(mode="json"),
        ],
        "llm_messages": [],
    }

    result = await supervisor_synthesis_node(state)

    assert calls == [
        ("PROPOSE_REFUND", {"orderItemId": "i1", "runId": "root-1"}, "u1")
    ]
    assert result["tools_called"] == ["PROPOSE_REFUND"]
    assert "act_0123456789abcdef0123456789abcdef" in result["llm_messages"][-1].content


@pytest.mark.asyncio
async def test_supervisor_synthesis_orders_artifacts_by_plan_not_completion(monkeypatch):
    artifact_orders: list[list[str]] = []

    async def capture_synthesis(_llm, messages, **_kwargs):
        payload = json.loads(messages[1].content)
        artifact_orders.append([item["agent_id"] for item in payload["artifacts"]])
        return AIMessage(content="已按计划汇总。")

    monkeypatch.setattr("app.graph.multi_agent.rt.bind_agent_llm", lambda **_kwargs: object())
    monkeypatch.setattr(
        "app.graph.multi_agent.invoke_llm_with_metrics", capture_synthesis
    )
    monkeypatch.setattr(
        "app.graph.multi_agent.episode_service.record_step", lambda *args, **kwargs: None
    )
    planned = [
        "order_fulfillment_specialist",
        "after_sales_policy_specialist",
    ]
    result = await supervisor_synthesis_node(
        {
            "agent_msg": {"runId": "root-stable-join"},
            "user_text": "订单延迟且想了解退款政策",
            "supervisor_plan": SupervisorPlan(
                specialists=planned,
                goals={agent_id: "核验事实" for agent_id in planned},
            ).model_dump(mode="json"),
            "specialist_artifacts": [
                AgentArtifact(
                    status="SUCCESS",
                    agent_id="after_sales_policy_specialist",
                    evidence=[{"type": "knowledge", "documentId": "policy-1"}],
                    draft_answer="政策证据",
                ).model_dump(mode="json"),
                AgentArtifact(
                    status="SUCCESS",
                    agent_id="order_fulfillment_specialist",
                    evidence=[{"type": "order", "orderId": "masked"}],
                    draft_answer="订单事实",
                ).model_dump(mode="json"),
            ],
        }
    )

    assert artifact_orders == [planned]
    assert [item["type"] for item in result["rag_source_refs"]] == [
        "order",
        "knowledge",
    ]


@pytest.mark.asyncio
async def test_failed_root_proposal_never_exposes_confirmation_ui(monkeypatch):
    async def fake_llm(*_args, **_kwargs):
        return AIMessage(content="订单已核验，请确认取消。")

    async def rejected_tool(*_args, **_kwargs):
        return ToolInvokeResult(
            content="订单状态不允许取消【act_0123456789abcdef0123456789abcdef】",
            success=False,
            error_code="ORDER_STATUS_INVALID",
            assistant_cards='{"type":"ACTION_CONFIRM"}',
        )

    monkeypatch.setattr("app.graph.multi_agent.rt.bind_agent_llm", lambda **_kwargs: object())
    monkeypatch.setattr("app.graph.multi_agent.invoke_llm_with_metrics", fake_llm)
    monkeypatch.setattr("app.graph.multi_agent.mcp_tool_router.invoke", rejected_tool)
    monkeypatch.setattr(
        "app.graph.multi_agent.episode_service.record_step", lambda *args, **kwargs: None
    )
    result = await supervisor_synthesis_node(
        {
            "agent_msg": {"runId": "root-rejected-action"},
            "user_id": "u1",
            "user_text": "取消订单",
            "verified_order_context": {"orderId": "o1"},
            "supervisor_plan": SupervisorPlan(
                intent="CANCEL_ORDER",
                specialists=["order_fulfillment_specialist"],
                requires_action=True,
                action_type="PROPOSE_CANCEL_ORDER",
            ).model_dump(mode="json"),
            "specialist_artifacts": [
                AgentArtifact(
                    status="SUCCESS",
                    agent_id="order_fulfillment_specialist",
                    evidence=[{"type": "order", "orderId": "masked"}],
                    draft_answer="订单已核验",
                ).model_dump(mode="json")
            ],
        }
    )

    assert result["action_proposal"]["reason"] == "ORDER_STATUS_INVALID"
    assert "操作确认未创建" in result["chunks"][0]
    assert "assistant_cards" not in result


@pytest.mark.asyncio
async def test_supervisor_plan_falls_back_without_leaving_multi_agent_path(monkeypatch):
    async def invalid_structured_plan(*_args, **_kwargs):
        raise ValueError("bad structured output")

    for name in ("start_child_run", "record_handoff", "record_step"):
        monkeypatch.setattr(
            f"app.graph.multi_agent.episode_service.{name}", lambda *args, **kwargs: None
        )
    monkeypatch.setattr(
        "app.graph.multi_agent._structured_supervisor_plan", invalid_structured_plan
    )
    result = await supervisor_plan_node(
        {
            "agent_msg": {"runId": "root-1", "sessionId": "session-1"},
            "message_id": 7,
            "user_id": "u1",
            "user_text": "查一下订单",
            "intent": "QUERY_ORDER",
        }
    )

    assert result["supervisor_plan"]["planner_source"] == "DETERMINISTIC_FALLBACK"
    assert result["route"] == "multi_agent_fanout"
    assert [task["agent_id"] for task in result["specialist_tasks"]] == [
        "order_fulfillment_specialist"
    ]
    assert prepare_specialist_sends(result)[0].node == "specialist_runner"


@pytest.mark.asyncio
async def test_shopping_handoff_only_contains_allowlisted_profile_context(monkeypatch):
    async def deterministic_plan(_state, fallback):
        return fallback

    async def profile_with_private_metadata(_user_id):
        return {
            "category": "手机",
            "budgetMax": 3000,
            "brands": ["华为"],
            "excludedBrands": [],
            "scenarios": ["办公"],
            "features": ["续航"],
            "acceptSubstitute": True,
            "email": "must-not-leak@example.com",
            "fieldMeta": {"brands": {"sourceMessageId": 42}},
            "revision": 9,
        }

    monkeypatch.setattr(
        "app.graph.multi_agent._structured_supervisor_plan", deterministic_plan
    )
    monkeypatch.setattr(
        "app.graph.multi_agent.shopping_profile_service.get_effective_profile",
        profile_with_private_metadata,
    )
    for name in ("start_child_run", "record_handoff", "record_step"):
        monkeypatch.setattr(
            f"app.graph.multi_agent.episode_service.{name}", lambda *args, **kwargs: None
        )

    result = await supervisor_plan_node(
        {
            "agent_msg": {"runId": "root-shopping", "sessionId": "session-1"},
            "message_id": 8,
            "user_id": "u1",
            "user_text": "推荐一款手机",
            "intent": "PRODUCT_SEARCH",
            "llm_messages": ["private-root-history"],
        }
    )

    task = SpecialistTask.model_validate(result["specialist_tasks"][0])
    assert task.session_summary == "预算不超过3000元、偏好华为、类别手机、场景办公、关注续航、可接受同类替代"
    assert task.verified_context["shoppingProfile"] == {
        "category": "手机",
        "budgetMax": 3000,
        "brands": ["华为"],
        "scenarios": ["办公"],
        "features": ["续航"],
        "acceptSubstitute": True,
    }
    serialized = str(result["specialist_tasks"])
    assert "must-not-leak" not in serialized
    assert "fieldMeta" not in serialized
    assert "private-root-history" not in serialized


@pytest.mark.asyncio
async def test_specialist_handoff_redacts_pii_and_drops_raw_intent_data(monkeypatch):
    async def deterministic_plan(_state, fallback):
        return fallback

    monkeypatch.setattr(
        "app.graph.multi_agent._structured_supervisor_plan", deterministic_plan
    )
    for name in ("start_child_run", "record_handoff", "record_step"):
        monkeypatch.setattr(
            f"app.graph.multi_agent.episode_service.{name}", lambda *args, **kwargs: None
        )

    result = await supervisor_plan_node(
        {
            "agent_msg": {"runId": "root-private", "sessionId": "session-1"},
            "message_id": 9,
            "user_id": "u1",
            "user_text": (
                "查订单，邮箱 alice@example.com，手机号 13812345678，"
                "收货地址是北京市朝阳区望京路1号，订单号 o-safe"
            ),
            "intent": "QUERY_ORDER",
            "intent_data": "alice@example.com 13812345678 北京市朝阳区望京路1号",
            "verified_order_context": {
                "orderId": "o-safe",
                "orderStatus": 2,
                "_searchText": "alice@example.com",
            },
        }
    )

    task = SpecialistTask.model_validate(result["specialist_tasks"][0])
    serialized = str(task.model_dump(mode="json"))
    assert "alice@example.com" not in serialized
    assert "13812345678" not in serialized
    assert "北京市朝阳区望京路1号" not in serialized
    assert "intentData" not in serialized
    assert "_searchText" not in serialized
    assert task.verified_context["order"] == {"orderId": "o-safe", "orderStatus": 2}
    assert task.user_text.count("REDACTED") == 3


@pytest.mark.asyncio
async def test_action_executor_fails_closed_when_required_policy_evidence_is_missing(
    monkeypatch,
):
    async def fake_llm(*_args, **_kwargs):
        return AIMessage(content="订单已核验，但政策依据不足。")

    router = AsyncMock()
    monkeypatch.setattr("app.graph.multi_agent.rt.bind_agent_llm", lambda **_kwargs: object())
    monkeypatch.setattr("app.graph.multi_agent.invoke_llm_with_metrics", fake_llm)
    monkeypatch.setattr("app.graph.multi_agent.mcp_tool_router.invoke", router)
    monkeypatch.setattr(
        "app.graph.multi_agent.episode_service.record_step", lambda *args, **kwargs: None
    )
    state = {
        "agent_msg": {"runId": "root-blocked"},
        "user_id": "u1",
        "user_text": "帮我退款",
        "verified_order_context": {"orderId": "o1", "orderItemId": "i1"},
        "supervisor_plan": SupervisorPlan(
            intent="REFUND",
            specialists=[
                "order_fulfillment_specialist",
                "after_sales_policy_specialist",
            ],
            requires_action=True,
            action_type="PROPOSE_REFUND",
        ).model_dump(mode="json"),
        "specialist_artifacts": [
            AgentArtifact(
                status="SUCCESS",
                agent_id="order_fulfillment_specialist",
                draft_answer="订单已核验",
                evidence=[{"type": "order", "id": "masked"}],
            ).model_dump(mode="json"),
            AgentArtifact(
                status="DEGRADED",
                agent_id="after_sales_policy_specialist",
                draft_answer="没有政策依据",
                evidence=[],
            ).model_dump(mode="json"),
        ],
    }

    result = await supervisor_synthesis_node(state)

    router.assert_not_awaited()
    assert result["tools_called"] == []
    assert result["action_proposal"]["success"] is False
    assert result["action_proposal"]["reason"] == "POLICY_EVIDENCE_INSUFFICIENT"
    assert "暂不创建操作确认" in result["chunks"][0]


@pytest.mark.asyncio
async def test_action_executor_rejects_failed_order_tool_even_with_source_ref(monkeypatch):
    async def fake_llm(*_args, **_kwargs):
        return AIMessage(content="订单查询失败。")

    router = AsyncMock()
    monkeypatch.setattr("app.graph.multi_agent.rt.bind_agent_llm", lambda **_kwargs: object())
    monkeypatch.setattr("app.graph.multi_agent.invoke_llm_with_metrics", fake_llm)
    monkeypatch.setattr("app.graph.multi_agent.mcp_tool_router.invoke", router)
    monkeypatch.setattr(
        "app.graph.multi_agent.episode_service.record_step", lambda *args, **kwargs: None
    )
    state = {
        "agent_msg": {"runId": "root-order-failed"},
        "user_id": "u1",
        "user_text": "取消订单",
        "verified_order_context": {"orderId": "o1"},
        "supervisor_plan": SupervisorPlan(
            intent="CANCEL_ORDER",
            specialists=["order_fulfillment_specialist"],
            requires_action=True,
            action_type="PROPOSE_CANCEL_ORDER",
        ).model_dump(mode="json"),
        "specialist_artifacts": [
            AgentArtifact(
                status="SUCCESS",
                agent_id="order_fulfillment_specialist",
                draft_answer="订单查询失败",
                evidence=[
                    {"type": "tool_result", "tool": "QUERY_ORDERS", "success": False},
                    {"type": "order", "id": "untrusted-after-failure"},
                ],
            ).model_dump(mode="json")
        ],
    }

    result = await supervisor_synthesis_node(state)

    router.assert_not_awaited()
    assert result["action_proposal"]["reason"] == "ORDER_EVIDENCE_INSUFFICIENT"


class _ProductionHarnessState(TypedDict, total=False):
    agent_msg: dict
    user_id: str
    message_id: int
    user_text: str
    intent: str
    verified_order_context: dict
    llm_messages: list
    supervisor_plan: dict
    specialist_tasks: list[dict]
    specialist_task: dict
    specialist_artifacts: Annotated[list[dict], operator.add]
    chunks: Annotated[list[str], operator.add]
    tools_called: Annotated[list[str], operator.add]
    rag_source_refs: list[dict]
    route: str
    assistant_cards: str
    action_proposal: dict


@pytest.mark.asyncio
async def test_production_harness_fans_out_joins_and_proposes_once(monkeypatch):
    read_calls_started = asyncio.Event()
    tool_calls: list[tuple[str, dict, str]] = []
    trace_events: list[str] = []

    async def deterministic_plan(_state, fallback):
        return fallback

    async def fake_llm(_llm, messages, **_kwargs):
        system = str(messages[0].content)
        has_tool_result = any(isinstance(message, ToolMessage) for message in messages)
        if "order_fulfillment_specialist" in system:
            if not has_tool_result:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "logistics-read",
                            "name": "QUERY_LOGISTICS",
                            "args": {"orderId": "o1"},
                        }
                    ],
                )
            return AIMessage(content="订单物流状态已核验，当前存在延迟。")
        if "after_sales_policy_specialist" in system:
            if not has_tool_result:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "policy-read",
                            "name": "SEARCH_KNOWLEDGE",
                            "args": {"query": "延迟订单退款政策"},
                        }
                    ],
                )
            return AIMessage(content="退款资格需按政策核验，并由用户确认后提交。")
        assert "电商 Supervisor" in system
        return AIMessage(content="物流和退款政策均已核验，请确认是否提交退款申请。")

    async def fake_tool(name, args, user_id, **_kwargs):
        tool_calls.append((name, args, user_id))
        if name in {"QUERY_LOGISTICS", "SEARCH_KNOWLEDGE"}:
            if len(tool_calls) == 2:
                read_calls_started.set()
            await asyncio.wait_for(read_calls_started.wait(), timeout=0.5)
        if name == "QUERY_LOGISTICS":
            return ToolInvokeResult(
                content="物流已延迟",
                source_refs=[{"type": "order", "orderId": "masked-o1"}],
            )
        if name == "SEARCH_KNOWLEDGE":
            return ToolInvokeResult(
                content="命中退款政策",
                source_refs=[{"type": "knowledge", "documentId": "policy-1"}],
            )
        assert name == "PROPOSE_REFUND"
        return ToolInvokeResult(
            content="已生成退款确认卡【act_0123456789abcdef0123456789abcdef】",
            assistant_cards='{"type":"ACTION_CONFIRM"}',
        )

    monkeypatch.setattr(
        "app.graph.multi_agent._structured_supervisor_plan", deterministic_plan
    )
    monkeypatch.setattr(
        "app.graph.multi_agent.rt.bind_agent_llm", lambda **_kwargs: object()
    )
    monkeypatch.setattr("app.graph.multi_agent.invoke_llm_with_metrics", fake_llm)
    monkeypatch.setattr("app.graph.multi_agent.mcp_tool_router.invoke", fake_tool)
    for name in ("start_child_run", "record_handoff", "finish_run"):
        monkeypatch.setattr(
            f"app.graph.multi_agent.episode_service.{name}", lambda *args, **kwargs: None
        )
    monkeypatch.setattr(
        "app.graph.multi_agent.episode_service.record_step",
        lambda event, **_kwargs: trace_events.append(event),
    )

    graph = StateGraph(_ProductionHarnessState)
    graph.add_node("multi_agent_plan", supervisor_plan_node)
    graph.add_node("specialist_runner", specialist_runner_node)
    graph.add_node("multi_agent_synthesis", supervisor_synthesis_node)
    graph.set_entry_point("multi_agent_plan")
    graph.add_conditional_edges(
        "multi_agent_plan",
        prepare_specialist_sends,
        {
            "specialist_runner": "specialist_runner",
            "multi_agent_synthesis": "multi_agent_synthesis",
        },
    )
    graph.add_edge("specialist_runner", "multi_agent_synthesis")
    graph.add_edge("multi_agent_synthesis", END)

    result = await graph.compile().ainvoke(
        {
            "agent_msg": {"runId": "root-production", "sessionId": "session-1"},
            "message_id": 9,
            "user_id": "u1",
            "user_text": "订单延迟了，现在帮我申请退款",
            "intent": "REFUND",
            "verified_order_context": {"orderId": "o1", "orderItemId": "i1"},
            "llm_messages": [],
            "specialist_artifacts": [],
            "chunks": [],
            "tools_called": [],
        }
    )

    assert [call[0] for call in tool_calls[:2]] == [
        "QUERY_LOGISTICS",
        "SEARCH_KNOWLEDGE",
    ] or [call[0] for call in tool_calls[:2]] == [
        "SEARCH_KNOWLEDGE",
        "QUERY_LOGISTICS",
    ]
    assert tool_calls[-1] == (
        "PROPOSE_REFUND",
        {"orderItemId": "i1", "runId": "root-production"},
        "u1",
    )
    assert sorted(item["agent_id"] for item in result["specialist_artifacts"]) == [
        "after_sales_policy_specialist",
        "order_fulfillment_specialist",
    ]
    assert result["tools_called"] == ["PROPOSE_REFUND"]
    proposal = result["action_proposal"]
    assert proposal["tool"] == "PROPOSE_REFUND"
    assert proposal["arguments"] == {"orderItemId": "i1", "runId": "root-production"}
    assert proposal["success"] is True
    assert proposal["requires_confirmation"] is True
    assert {item["type"] for item in proposal["evidence_refs"]} == {"order", "knowledge"}
    assert proposal["reason"] is None
    assert trace_events.count("SPECIALIST_STARTED") == 2
    assert result["chunks"] == ["物流和退款政策均已核验，请确认是否提交退款申请。"]


@pytest.mark.asyncio
async def test_data_analyst_clarifies_ambiguous_sales_ranking_without_model():
    plan = await DataAnalystService()._plan("最近七天最好卖的商品是什么？")
    assert plan.status == "NEEDS_CLARIFICATION"
    assert "销售金额" in plan.clarification_question


def test_sql_guard_accepts_catalog_query_and_rejects_escape_attempts():
    valid = validate_sql(
        "SELECT date, gross_paid_amount FROM analytics_sales_daily "
        "WHERE date BETWEEN '2026-08-01' AND '2026-08-07' ORDER BY date LIMIT 200"
    )
    assert valid.allowed
    rejected = {
        "DELETE FROM analytics_sales_daily LIMIT 1": "SQL_NOT_SELECT",
        "SELECT * FROM analytics_sales_daily LIMIT 1": "SQL_STAR_FORBIDDEN",
        "SELECT date FROM aishop_admin.analytics_sales_daily LIMIT 1": "SQL_CROSS_DATABASE_FORBIDDEN",
        "SELECT SLEEP(2) FROM analytics_sales_daily LIMIT 1": "SQL_DANGEROUS_FUNCTION",
        "SELECT date FROM analytics_sales_daily": "SQL_LIMIT_REQUIRED",
        "SELECT date FROM analytics_sales_daily LIMIT 201": "SQL_LIMIT_EXCEEDED",
        "SELECT date FROM analytics_sales_daily; SELECT 1": "SQL_MULTI_STATEMENT",
        "SELECT email FROM analytics_sales_daily LIMIT 1": "SQL_COLUMN_NOT_ALLOWLISTED",
        "SELECT date FROM analytics_sales_daily LIMIT 1": "SQL_DATE_RANGE_REQUIRED",
        (
            "SELECT date FROM analytics_sales_daily WHERE date BETWEEN '2026-01-01' "
            "AND '2026-04-01' LIMIT 1"
        ): "SQL_DATE_RANGE_EXCEEDED",
        (
            "SELECT date FROM analytics_sales_daily WHERE date BETWEEN '2026-08-01' "
            "AND '2026-08-07' OR 1=1 LIMIT 1"
        ): "SQL_OR_FORBIDDEN",
        (
            "SELECT date FROM analytics_sales_daily WHERE date BETWEEN '2026-08-01' "
            "AND '2026-08-07' LIMIT 1 FOR UPDATE"
        ): "SQL_LOCK_FORBIDDEN",
        (
            "SELECT @x := gross_paid_amount FROM analytics_sales_daily WHERE date BETWEEN "
            "'2026-08-01' AND '2026-08-07' LIMIT 1"
        ): "SQL_VARIABLE_FORBIDDEN",
        (
            "SELECT date, @@version FROM analytics_sales_daily WHERE date BETWEEN "
            "'2026-08-01' AND '2026-08-07' LIMIT 1"
        ): "SQL_VARIABLE_FORBIDDEN",
        (
            "SELECT date FROM analytics_sales_daily WHERE date BETWEEN '2026-08-01' "
            "AND '2026-08-07' LIMIT 1 OFFSET 1"
        ): "SQL_OFFSET_FORBIDDEN",
        (
            "SELECT CURRENT_USER() FROM analytics_sales_daily WHERE date BETWEEN "
            "'2026-08-01' AND '2026-08-07' LIMIT 1"
        ): "SQL_FUNCTION_NOT_ALLOWLISTED",
    }
    for sql, reason in rejected.items():
        result = validate_sql(sql)
        assert not result.allowed, sql
        assert result.reason == reason, sql

    inventory = validate_sql(
        "SELECT product_name, stock, risk_level FROM analytics_inventory_risk "
        "ORDER BY stock LIMIT 20"
    )
    assert inventory.allowed

    mismatched = validate_sql(
        "SELECT date FROM analytics_sales_daily WHERE date BETWEEN '2026-08-01' "
        "AND '2026-08-07' LIMIT 20",
        expected_view="analytics_agent_quality_daily",
    )
    assert not mismatched.allowed
    assert mismatched.reason == "SQL_PLAN_VIEW_MISMATCH"

    controlled_cte = validate_sql(
        "WITH scoped AS (SELECT date, gross_paid_amount FROM analytics_sales_daily "
        "WHERE date BETWEEN '2026-08-01' AND '2026-08-07') "
        "SELECT date, gross_paid_amount FROM scoped ORDER BY date LIMIT 20"
    )
    assert controlled_cte.allowed

    count_star = validate_sql(
        "SELECT COUNT(*) FROM analytics_sales_daily "
        "WHERE date BETWEEN '2026-08-01' AND '2026-08-07' LIMIT 1"
    )
    assert count_star.allowed

    qualified_star = validate_sql(
        "SELECT analytics_inventory_risk.* FROM analytics_inventory_risk LIMIT 20"
    )
    assert not qualified_star.allowed
    assert qualified_star.reason == "SQL_STAR_FORBIDDEN"

    alias_collision = validate_sql(
        "SELECT 1 AS email, email FROM analytics_inventory_risk LIMIT 20"
    )
    assert not alias_collision.allowed
    assert alias_collision.reason == "SQL_COLUMN_NOT_ALLOWLISTED"

    escaped_cte_alias = validate_sql(
        "WITH scoped AS (SELECT gross_paid_amount AS email "
        "FROM analytics_sales_daily WHERE date BETWEEN '2026-08-01' AND '2026-08-07') "
        "SELECT email FROM scoped LIMIT 20"
    )
    assert not escaped_cte_alias.allowed
    assert escaped_cte_alias.reason == "SQL_COLUMN_NOT_ALLOWLISTED"

    oversized = validate_sql("SELECT " + "1," * 5_000 + "1")
    assert not oversized.allowed
    assert oversized.reason == "SQL_TOO_LONG"
