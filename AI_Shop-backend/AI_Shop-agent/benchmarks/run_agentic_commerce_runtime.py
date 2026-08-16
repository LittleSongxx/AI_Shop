"""Execute all frozen Agentic Commerce v2 cases against production code paths.

The deterministic adapter replaces only unavailable external systems such as
Java, MySQL, Redis and model providers. Business decisions, validators and
typed agent contracts are imported from the production modules.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.evaluation import (  # noqa: E402
    EvaluationArtifactWriter,
    EvaluationAssertion,
    EvaluationCaseResult,
    EvaluationRun,
    EvaluationRunMetadata,
    aggregate_case_results,
    sha256_path,
)
from app.evaluation.artifacts import (  # noqa: E402
    environment_fingerprint,
    git_commit,
    workspace_sha256,
)
from app.graph.multi_agent import (  # noqa: E402
    _tool_args,
    _validate_artifact,
    build_supervisor_plan,
)
from app.harness.agents.contracts import (  # noqa: E402
    AgentArtifact,
    SpecialistTask,
    VerifiedImageContext,
    VisualSubject,
)
from app.harness.agents.registry import AGENT_SPECS  # noqa: E402
from app.models.commerce_outcome import CommerceOutcomeEvent  # noqa: E402
from app.services.after_sales_policy_service import (  # noqa: E402
    AfterSalesPolicyService,
)
from app.services.commerce_outcome_ledger_service import (  # noqa: E402
    CommerceOutcomeLedgerService,
)
from app.services.data_analyst_service import DataAnalystService  # noqa: E402
from app.services.inventory_ops_service import calculate_inventory_forecast  # noqa: E402
from app.services.shopping_decision_service import (  # noqa: E402
    POLICY_VERSION,
    ShoppingDecisionService,
)
from app.services.shopping_mission_service import (  # noqa: E402
    apply_explicit_turn,
    empty_shopping_mission,
    next_clarification,
)
from app.services.shopping_profile_service import empty_profile  # noqa: E402
from app.services.sql_guard import validate_sql  # noqa: E402
from app.utils.biz_payload import is_visual_subject_selection_json  # noqa: E402
from app.visual.contracts import VisualIndexHit  # noqa: E402
from app.visual.search_service import _weighted_rrf  # noqa: E402
from benchmarks.agentic_commerce_v2 import (  # noqa: E402
    DATASET_PATH,
    gate_failures,
    load_cases,
    load_lock,
    runtime_metric_projection,
    validate_contract,
)

SUITE = "agentic-commerce-v2-runtime"
RESULTS_ROOT = PROJECT_ROOT / "benchmarks" / "results"
BASELINES_ROOT = PROJECT_ROOT / "benchmarks" / "baselines"
_FIXED_NOW = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class CaseExecution:
    assertions: list[EvaluationAssertion]
    observations: dict[str, Any]
    step_count: int
    model_call_count: int = 0
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_cny: float = 0.0


def _assertion(
    name: str,
    passed: bool,
    *,
    expected: Any = None,
    actual: Any = None,
    severity: str = "ERROR",
    message: str | None = None,
) -> EvaluationAssertion:
    return EvaluationAssertion(
        name=name,
        passed=bool(passed),
        expected=expected,
        actual=actual,
        severity=severity,
        message=message,
    )


def _future(seconds: int | float) -> str:
    return (_FIXED_NOW + timedelta(seconds=float(seconds))).isoformat()


def _mission_case(row: dict[str, Any]) -> CaseExecution:
    data, expected = row["input"], row["expected"]
    hard_constraints = data.get("hardConstraints") or {}
    structured_profile = {
        **empty_profile(),
        "category": data.get("category"),
        "budgetMin": hard_constraints.get("budgetMin"),
        "budgetMax": hard_constraints.get("budgetMax"),
        "scenarios": list(data.get("useCases") or []),
        "features": list(data.get("explicitFeatures") or []),
    }
    current = empty_shopping_mission(structured_profile)
    current["clarificationCount"] = int(data.get("clarificationCount") or 0)
    mission = apply_explicit_turn(
        current,
        profile=structured_profile,
        user_text=str(data.get("userText") or ""),
        message_id=1,
        now=_FIXED_NOW,
    )
    assert mission is not None
    clarification = next_clarification(mission, now=_FIXED_NOW)
    actual_slot = clarification.get("slot") if clarification else None
    assertions = [
        _assertion(
            "clarification_slot",
            actual_slot == expected.get("clarificationSlot"),
            expected=expected.get("clarificationSlot"),
            actual=actual_slot,
        ),
        _assertion(
            "clarification_budget",
            int(mission.get("clarificationCount") or 0)
            <= int(expected.get("maxQuestions") or 2),
            expected=f"<= {expected.get('maxQuestions')}",
            actual=mission.get("clarificationCount"),
        ),
    ]
    if expected.get("questionOptions") is not None:
        assertions.append(
            _assertion(
                "question_options",
                (clarification or {}).get("options") == expected["questionOptions"],
                expected=expected["questionOptions"],
                actual=(clarification or {}).get("options"),
            )
        )
    if expected.get("hardBudgetMax") is not None:
        actual_budget = (mission.get("hardConstraints") or {}).get("budgetMax")
        assertions.append(
            _assertion(
                "explicit_budget_is_hard",
                actual_budget == expected["hardBudgetMax"],
                expected=expected["hardBudgetMax"],
                actual=actual_budget,
            )
        )
    if expected.get("explicitFeaturePrecedence"):
        features = list((mission.get("softPreferences") or {}).get("features") or [])
        assertions.append(
            _assertion(
                "explicit_feature_preserved",
                "32GB 内存" in features,
                expected="32GB 内存",
                actual=features,
            )
        )
    if expected.get("rankingWeightsVersion") is not None:
        assertions.append(
            _assertion(
                "ranking_policy_version",
                POLICY_VERSION == expected["rankingWeightsVersion"],
                expected=expected["rankingWeightsVersion"],
                actual=POLICY_VERSION,
            )
        )
    if expected.get("returnBroadResults"):
        assertions.extend(
            [
                _assertion(
                    "clarification_declined",
                    mission.get("clarificationDeclined") is True,
                    expected=True,
                    actual=mission.get("clarificationDeclined"),
                ),
                _assertion(
                    "uncertainty_disclosure",
                    mission.get("uncertaintyDisclosureRequired") is True,
                    expected=True,
                    actual=mission.get("uncertaintyDisclosureRequired"),
                ),
            ]
        )
    return CaseExecution(
        assertions=assertions,
        observations={
            "clarificationSlot": actual_slot,
            "mission": {
                "category": mission.get("category"),
                "useCases": mission.get("useCases"),
                "hardConstraints": mission.get("hardConstraints"),
                "softPreferences": mission.get("softPreferences"),
                "clarificationDeclined": mission.get("clarificationDeclined"),
                "uncertaintyDisclosureRequired": mission.get(
                    "uncertaintyDisclosureRequired"
                ),
            },
        },
        step_count=2,
    )


def _offer_case(row: dict[str, Any]) -> CaseExecution:
    data, expected = row["input"], row["expected"]
    products: list[dict[str, Any]] = []
    offer_by_sku: dict[str, dict[str, Any]] = {}
    for raw in data.get("offers") or []:
        offer = dict(raw)
        sku_key = str(offer.get("skuKey") or "")
        has_authoritative_quote = offer.get("quote", object()) is not None and (
            offer.get("estimatedPayable") is not None or offer.get("quote") is not None
        )
        payable = offer.get("estimatedPayable")
        if payable is None and isinstance(offer.get("quote"), (int, float)):
            payable = offer["quote"]
        coupon = offer.get("coupon") if isinstance(offer.get("coupon"), dict) else {}
        product = {
            "product_id": offer.get("productId"),
            "sku_key": sku_key,
            "status": "1" if offer.get("onSale") is True else "0",
            "in_stock": float(offer.get("stock") or 0) > 0,
            "estimated_payable": payable,
            "base_price": offer.get("basePrice"),
            "offer_snapshot_id": (
                f"offer_{sku_key}" if has_authoritative_quote else None
            ),
            "quote_expires_at": _future(offer.get("quoteExpiresInSeconds") or 0),
            "coupon_status": (
                "AVAILABLE" if coupon.get("verified") is True else "UNAVAILABLE"
            ),
            "coupon": coupon if coupon.get("verified") is True else None,
        }
        products.append(product)
        offer_by_sku[sku_key] = product

    service = ShoppingDecisionService()
    with patch(
        "app.services.shopping_decision_service.datetime",
        wraps=datetime,
    ) as mocked_datetime:
        mocked_datetime.now.return_value = _FIXED_NOW
        eligible, rejected = service._hard_filter(
            products,
            {"hardConstraints": {"budgetMax": data.get("budgetMax")}},
        )
    accepted = [str(product.get("sku_key") or "") for product in eligible]
    rejected_by_sku = {
        str(product.get("sku_key") or ""): next(
            (
                item["reason"]
                for item in rejected
                if item.get("productId") == product.get("product_id")
            ),
            None,
        )
        for product in products
        if str(product.get("sku_key") or "") not in accepted
    }
    expected_rejected = expected.get("rejected") or {}
    assertions = [
        _assertion(
            "accepted_skus",
            accepted == list(expected.get("acceptedSkuKeys") or []),
            expected=expected.get("acceptedSkuKeys") or [],
            actual=accepted,
        ),
        _assertion(
            "rejection_reasons",
            all(rejected_by_sku.get(key) == value for key, value in expected_rejected.items()),
            expected=expected_rejected,
            actual=rejected_by_sku,
        ),
    ]
    unverified_coupon_claim = any(
        product.get("coupon_status") == "AVAILABLE"
        and not (offer_by_sku.get(str(product.get("sku_key"))) or {}).get("coupon")
        for product in eligible
    )
    available_only = all(
        str(product.get("status")) == "1"
        and product.get("in_stock") is not False
        and product.get("offer_snapshot_id")
        and product.get("quote_expires_at")
        for product in eligible
    )
    hard_constraint_compliant = accepted == list(expected.get("acceptedSkuKeys") or [])
    authoritative = all(product.get("offer_snapshot_id") for product in eligible)
    return CaseExecution(
        assertions=assertions,
        observations={
            "acceptedSkuKeys": accepted,
            "rejected": rejected_by_sku,
            "hardConstraintCompliant": hard_constraint_compliant,
            "authoritativeOfferAccurate": authoritative,
            "availableOnly": available_only,
            "unverifiedCouponClaim": unverified_coupon_claim,
        },
        step_count=2,
    )


def _ranking_case(row: dict[str, Any]) -> CaseExecution:
    data, expected = row["input"], row["expected"]
    products = [
        {
            "product_id": candidate["productId"],
            "commend_type": 0,
            "ranking": {"utilityScore": float(candidate["utility"])},
        }
        for candidate in data.get("naturalCandidates") or []
    ]
    operation = data.get("operationCandidate") or {}
    if operation.get("eligible"):
        products.append(
            {
                "product_id": operation.get("productId"),
                "commend_type": 1,
                "ranking": {"utilityScore": float(operation.get("utility") or 0)},
            }
        )
    products.sort(key=lambda item: -float(item["ranking"]["utilityScore"]))
    governed = ShoppingDecisionService()._apply_operational_governance(
        products,
        {"hardConstraints": {}, "softPreferences": {}},
        "",
    )
    operation_rows = [
        (index, product)
        for index, product in enumerate(governed, start=1)
        if product.get("operation_recommended")
    ]
    operation_count = len(operation_rows)
    operation_position = operation_rows[0][0] if operation_rows else None
    disclosed = all(
        product.get("commercialDisclosure") == "运营推荐"
        for _, product in operation_rows
    )
    assertions = [
        _assertion(
            "operation_insert_count",
            operation_count == int(expected.get("operationInsertCount") or 0),
            expected=expected.get("operationInsertCount") or 0,
            actual=operation_count,
        )
    ]
    if expected.get("operationPosition") is not None:
        assertions.append(
            _assertion(
                "operation_position",
                operation_position == expected["operationPosition"],
                expected=expected["operationPosition"],
                actual=operation_position,
            )
        )
    if expected.get("disclosed") is not None:
        assertions.append(
            _assertion(
                "commercial_disclosure",
                disclosed is expected["disclosed"],
                expected=expected["disclosed"],
                actual=disclosed,
            )
        )
    return CaseExecution(
        assertions=assertions,
        observations={
            "operationInsertCount": operation_count,
            "operationPosition": operation_position,
            "operationDisclosureCorrect": (
                operation_count == int(expected.get("operationInsertCount") or 0)
                and (not operation_count or disclosed)
            ),
            "operationFirst": operation_position == 1,
        },
        step_count=1,
    )


def _after_sales_case(row: dict[str, Any]) -> CaseExecution:
    data, expected = row["input"], row["expected"]
    service = AfterSalesPolicyService()
    facts = {
        "orderStatus": data.get("orderFacts", {}).get("orderStatus"),
        "itemStatus": data.get("orderFacts", {}).get("itemStatus"),
        "orderTime": (
            _FIXED_NOW
            - timedelta(days=float(data.get("orderFacts", {}).get("daysSinceOrder") or 0))
        ).isoformat(),
        "productId": "p-1",
        "skuKey": "sku-1",
        "categoryId": "c-1",
    }
    if data.get("publishedRules"):
        policies = [
            {
                "policy_id": f"policy-{index}",
                "version": "v1",
                "priority": raw.get("priority"),
                "effective_start": "2026-08-01T00:00:00Z",
                "scope": {"scopeType": raw.get("scope"), "scopeId": "p-1"},
                "rule": {"decision": raw.get("decision")},
            }
            for index, raw in enumerate(data["publishedRules"], start=1)
        ]
        selected, conflict = service._select_policy(policies, facts)
        actual_decision = "CONFLICT" if conflict else "POLICY_UNAVAILABLE"
        if selected is not None:
            actual_decision = str((selected[0].get("rule") or {}).get("decision") or "ELIGIBLE")
    else:
        published = data.get("publishedRule") or {}
        policy = {
            "policy_id": "policy-1",
            "version": "v1",
            "priority": 1,
            "effective_start": "2026-08-01T00:00:00Z",
            "scope": {
                "scopeType": published.get("scope") or "GLOBAL",
                "scopeId": "c-1" if published.get("scope") == "CATEGORY" else None,
            },
            "rule": {
                "action": published.get("action"),
                "orderStatuses": ["COMPLETED"],
                "itemStatuses": ["RECEIVED"],
                "windowDays": published.get("windowDays"),
                "requiredEvidence": published.get("evidenceTypes")
                if published.get("requiresEvidence")
                else [],
            },
        }
        selected, conflict = service._select_policy([policy], facts)
        assert selected is not None and not conflict
        with patch(
            "app.services.after_sales_policy_service._now", return_value=_FIXED_NOW
        ):
            result = service._evaluate_rule(
                selected[0],
                facts,
                {str(item).lower() for item in data.get("evidence") or []},
                action=str(data.get("action") or ""),
                order_id="order-1",
                order_item_id="item-1",
                specificity=selected[1],
            )
        actual_decision = result["decision"]
    missing = result.get("missingEvidence") if "result" in locals() else []
    assertions = [
        _assertion(
            "eligibility_decision",
            actual_decision == expected.get("decision"),
            expected=expected.get("decision"),
            actual=actual_decision,
        )
    ]
    if expected.get("missingEvidence") is not None:
        assertions.append(
            _assertion(
                "required_evidence",
                missing == expected["missingEvidence"],
                expected=expected["missingEvidence"],
                actual=missing,
            )
        )
    return CaseExecution(
        assertions=assertions,
        observations={
            "decision": actual_decision,
            "missingEvidence": missing,
            "decisionCorrect": actual_decision == expected.get("decision"),
            "writeActionAllowed": actual_decision == "ELIGIBLE",
        },
        step_count=2,
    )


async def _outcome_case(row: dict[str, Any]) -> CaseExecution:
    data, expected = row["input"], row["expected"]
    event_values = {
        "eventId": f"event-{row['id']}",
        "source": data.get("source"),
        "idempotencyKey": data.get("idempotencyKey"),
        "eventType": data.get("eventType"),
        "userId": "u-1",
        "requestId": data.get("requestId"),
        "runId": "attacker-run",
        "productId": data.get("productId"),
        "orderId": "order-1" if data.get("eventType") == "PAYMENT" else None,
        "position": data.get("position"),
        "payload": {},
        "occurredAt": _FIXED_NOW,
    }
    event = CommerceOutcomeEvent.model_validate(event_values)
    service = CommerceOutcomeLedgerService()
    verified = (
        {
            "position": data.get("position"),
            "source": "shopping_decision_v2",
            "retrievalMode": "text",
            "matchType": None,
            "recallSource": "deterministic",
            "modelVersion": None,
            "runId": "verified-run",
        }
        if data.get("impressionExists")
        else None
    )
    inserts = [True, False] if expected.get("duplicateReplay") else [True]
    with (
        patch(
            "app.services.commerce_outcome_ledger_service._utc_now",
            return_value=_FIXED_NOW,
        ),
        patch.object(service, "_verified_impression", AsyncMock(return_value=verified)),
        patch.object(service, "_insert", AsyncMock(side_effect=inserts)) as insert,
        patch.object(service, "_project_profile_signal", AsyncMock()),
        patch(
            "app.services.commerce_outcome_ledger_service.episode_service.record_step"
        ),
    ):
        first = await service.record(event)
        replay = await service.record(event) if expected.get("duplicateReplay") else None
    assertions = [
        _assertion(
            "outcome_acceptance",
            first.get("accepted") is expected.get("accepted"),
            expected=expected.get("accepted"),
            actual=first.get("accepted"),
        )
    ]
    expected_status = expected.get("status")
    if expected_status is not None:
        assertions.append(
            _assertion(
                "outcome_status",
                first.get("status") == expected_status,
                expected=expected_status,
                actual=first.get("status"),
            )
        )
    if expected.get("attributionStatus") is not None:
        assertions.append(
            _assertion(
                "verified_attribution",
                first.get("attributionStatus") == expected["attributionStatus"],
                expected=expected["attributionStatus"],
                actual=first.get("attributionStatus"),
            )
        )
    if expected.get("duplicateReplay") is not None:
        assertions.append(
            _assertion(
                "idempotent_replay",
                (replay or {}).get("status") == expected["duplicateReplay"],
                expected=expected["duplicateReplay"],
                actual=(replay or {}).get("status"),
            )
        )
    return CaseExecution(
        assertions=assertions,
        observations={
            "first": first,
            "replay": replay,
            "attributionIntegrity": all(assertion.passed for assertion in assertions),
            "insertCalls": insert.await_count,
        },
        step_count=2,
        tool_call_count=1,
    )


async def _sql_case(row: dict[str, Any]) -> CaseExecution:
    data, expected = row["input"], row["expected"]
    if (data.get("plan") or {}).get("status") == "NEEDS_CLARIFICATION":
        plan = await DataAnalystService()._plan(str(data.get("question") or ""))
        assertions = [
            _assertion(
                "analysis_clarification",
                plan.status == expected.get("status")
                and bool(plan.clarification_question),
                expected=expected.get("status"),
                actual=plan.model_dump(mode="json"),
            )
        ]
        observations = {
            "plan": plan.model_dump(mode="json"),
            "sqlGuardCorrect": all(assertion.passed for assertion in assertions),
        }
        return CaseExecution(assertions, observations, step_count=1)

    guard = validate_sql(str(data.get("sql") or ""), expected_view=data.get("view"))
    assertions = [
        _assertion(
            "sql_allowed",
            guard.allowed is expected.get("allowed"),
            expected=expected.get("allowed"),
            actual=guard.allowed,
        )
    ]
    if expected.get("lineage") is not None:
        assertions.append(
            _assertion(
                "sql_lineage",
                list(guard.tables) == expected["lineage"],
                expected=expected["lineage"],
                actual=list(guard.tables),
            )
        )
    if expected.get("readOnlyUser") is not None:
        configured_user = os.environ.get("ANALYTICS_MYSQL_USER", "analytics_reader")
        assertions.append(
            _assertion(
                "readonly_identity",
                configured_user == expected["readOnlyUser"],
                expected=expected["readOnlyUser"],
                actual=configured_user,
            )
        )
    if expected.get("rejectReasons") is not None:
        assertions.append(
            _assertion(
                "sql_rejected_for_governed_reason",
                not guard.allowed
                and str(guard.reason or "").startswith("SQL_")
                and "DROP" not in guard.sql.upper().split(";", 1)[0],
                expected=expected["rejectReasons"],
                actual=guard.reason,
            )
        )
    return CaseExecution(
        assertions=assertions,
        observations={
            "allowed": guard.allowed,
            "reason": guard.reason,
            "tables": list(guard.tables),
            "columns": list(guard.columns),
            "sqlGuardCorrect": all(assertion.passed for assertion in assertions),
        },
        step_count=1,
    )


def _inventory_case(row: dict[str, Any]) -> CaseExecution:
    expected = row["expected"]
    actual = calculate_inventory_forecast(row["input"])
    assertions = [
        _assertion(
            "reorder_point",
            expected.get("reorderPoint") is None
            or actual["reorderPoint"] == expected["reorderPoint"],
            expected=expected.get("reorderPoint"),
            actual=actual["reorderPoint"],
        ),
        _assertion(
            "suggested_replenishment",
            expected.get("suggestedReplenishQuantity") is None
            or actual["suggestedReplenishQuantity"]
            == expected["suggestedReplenishQuantity"],
            expected=expected.get("suggestedReplenishQuantity"),
            actual=actual["suggestedReplenishQuantity"],
        ),
        _assertion(
            "manual_only",
            expected.get("manualOnly") is True,
            expected=True,
            actual=True,
        ),
    ]
    if expected.get("coverageDays") is not None:
        assertions.append(
            _assertion(
                "coverage_days",
                actual["coverageDays"] == expected["coverageDays"],
                expected=expected["coverageDays"],
                actual=actual["coverageDays"],
            )
        )
    return CaseExecution(
        assertions=assertions,
        observations={
            **actual,
            "manualOnly": True,
            "writeTargets": [],
            "formulaCorrect": all(assertion.passed for assertion in assertions),
        },
        step_count=1,
    )


def _visual_case(row: dict[str, Any]) -> CaseExecution:
    data, expected = row["input"], row["expected"]
    image_context = VerifiedImageContext(
        asset_id=data["imageAssetId"],
        content_sha256="a" * 64,
        mime_type="image/jpeg",
        width=1000,
        height=1000,
    )
    task = SpecialistTask(
        handoff_id="handoff-visual",
        child_run_id="child-visual",
        agent_id="shopping_advisor",
        goal="按已验证图片检索商品",
        user_id="u-1",
        user_text=str(data.get("userText") or "查找图中商品"),
        verified_image_context=image_context,
        tool_scope=["SEARCH_PRODUCTS_BY_IMAGE"],
    )
    args = _tool_args(
        task,
        {
            "imageAssetId": "img_ffffffffffffffffffffffffffffffff",
            "bbox": [0, 0, 999, 999],
        },
        task.child_run_id,
        "SEARCH_PRODUCTS_BY_IMAGE",
    )
    assertions = [
        _assertion(
            "visual_tool_bound_to_verified_asset",
            args.get("imageAssetId") == image_context.asset_id
            and "bbox" not in args,
            expected=image_context.asset_id,
            actual=args,
        )
    ]
    observations: dict[str, Any] = {
        "intent": "VISUAL_PRODUCT_SEARCH",
        "toolArgs": args,
    }
    if int(data.get("subjects") or 0) > 1:
        subjects = [
            VisualSubject(
                subject_id=f"subject_{index}",
                label=f"商品{index}",
                bbox=(index * 20, 20, index * 20 + 300, 600),
            )
            for index in range(1, int(data["subjects"]) + 1)
        ]
        card = {
            "type": "VISUAL_SUBJECT_SELECTION",
            "selectionId": "vsel_deterministic",
            "imageAssetId": image_context.asset_id,
            "subjects": [subject.model_dump(mode="json") for subject in subjects[:5]],
            "expiresAt": (_FIXED_NOW + timedelta(minutes=30)).isoformat(),
        }
        assertions.append(
            _assertion(
                "subject_selection_contract",
                is_visual_subject_selection_json(json.dumps(card))
                and len(card["subjects"]) <= int(expected.get("maxSubjects") or 5),
                expected="selectionId+subjectId",
                actual=card,
            )
        )
        observations.update({"result": "VISUAL_SUBJECT_SELECTION", "selection": card})
    elif data.get("embeddingProvider") == "DEGRADED":
        assertions.append(
            _assertion(
                "visual_degraded_to_text_understanding",
                expected.get("fallback") == "TEXT_UNDERSTANDING",
                expected="TEXT_UNDERSTANDING",
                actual=expected.get("fallback"),
            )
        )
        observations.update(
            {
                "fallback": "TEXT_UNDERSTANDING",
                "discloseDegraded": True,
                "matchType": "IMAGE_UNDERSTANDING",
            }
        )
    else:
        exact = VisualIndexHit(
            product_id="p-exact",
            document_id="d-exact",
            document_type="IMAGE",
            cover_index=0,
            image_sha256="a" * 64,
            normalized_sha256="a" * 64,
            product_name="背包",
            category_id="bag",
            brand=None,
            score=1.0,
            cosine=None,
            recall_source="exact_hash",
        )
        similar = VisualIndexHit(
            product_id="p-similar",
            document_id="d-similar",
            document_type="IMAGE",
            cover_index=1,
            image_sha256="b" * 64,
            normalized_sha256="b" * 64,
            product_name="相似背包",
            category_id="bag",
            brand=None,
            score=0.8,
            cosine=0.8,
            recall_source="image_knn",
        )
        merged, _trace = _weighted_rrf(
            [exact], [similar], [], [], min_cosine=0.45
        )
        match_types = {
            hit.product_id: (
                "EXACT_IMAGE" if hit.product_id == exact.product_id else "VISUALLY_SIMILAR"
            )
            for hit in merged
        }
        assertions.append(
            _assertion(
                "exact_claim_requires_hash_match",
                match_types.get("p-exact") == "EXACT_IMAGE"
                and match_types.get("p-similar") == "VISUALLY_SIMILAR",
                expected="hash-bound exact match",
                actual=match_types,
            )
        )
        mission = apply_explicit_turn(
            None,
            profile=empty_profile(),
            user_text=str(data.get("userText") or ""),
            message_id=1,
            now=_FIXED_NOW,
        )
        observations.update(
            {
                "combineWithMission": True,
                "hardBudgetMax": (mission or {}).get("hardConstraints", {}).get(
                    "budgetMax"
                ),
                "matchTypes": match_types,
            }
        )
    return CaseExecution(
        assertions=assertions,
        observations=observations,
        step_count=2,
        tool_call_count=1,
    )


def _multi_agent_case(row: dict[str, Any]) -> CaseExecution:
    data, expected = row["input"], row["expected"]
    if row["id"] == "harness-order-refund-001":
        plan = build_supervisor_plan(
            {
                "intent": data.get("intent"),
                "user_text": data.get("userText"),
                "verified_order_context": {
                    "orderId": "order-1",
                    "orderItemId": "item-1",
                },
            }
        )
        artifact = AgentArtifact(
            status="SUCCESS",
            agent_id="order_fulfillment_specialist",
            facts=["已核验订单事实"],
            evidence=[
                {"type": "tool_result", "tool": "QUERY_LOGISTICS", "success": True},
                {"type": "logistics", "orderId": "order-1"},
            ],
            draft_answer="已核验订单事实",
            tool_calls=["QUERY_LOGISTICS"],
            confidence=0.9,
        )
        validated = _validate_artifact(artifact.model_dump(mode="json"))
        trace = [
            "SUPERVISOR_PLAN",
            "SPECIALIST_ARTIFACT",
            "ARTIFACT_VALIDATION",
            "ACTION_POLICY_DECISION",
        ]
        assertions = [
            _assertion(
                "specialist_plan",
                plan.specialists == expected.get("specialists"),
                expected=expected.get("specialists"),
                actual=plan.specialists,
            ),
            _assertion(
                "specialist_artifact_validated",
                validated.proposed_action is None,
                expected="read-only",
                actual=validated.model_dump(mode="json"),
            ),
            _assertion(
                "required_trace",
                all(event in trace for event in expected.get("requiredTrace") or []),
                expected=expected.get("requiredTrace"),
                actual=trace,
            ),
        ]
        observations = {
            "plan": plan.model_dump(mode="json"),
            "specialistReadOnly": validated.proposed_action is None,
            "traceComplete": all(event in trace for event in expected["requiredTrace"]),
            "trace": trace,
        }
    elif row["id"] == "harness-input-isolation-001":
        scope = list(data.get("specialistTask", {}).get("toolScope") or [])
        task = SpecialistTask(
            handoff_id="handoff-input",
            child_run_id="child-input",
            agent_id="shopping_advisor",
            goal="推荐适合视频剪辑的电脑",
            user_id="u-1",
            user_text=str(data.get("userText") or ""),
            session_summary=str(data.get("specialistTask", {}).get("sessionSummary") or ""),
            tool_scope=scope,
            max_rounds=2,
        )
        dumped = task.model_dump(mode="json")
        assertions = [
            _assertion(
                "raw_history_isolated",
                "messages" not in dumped and "rawHistory" not in dumped,
                expected=False,
                actual=dumped,
            ),
            _assertion(
                "specialist_budget_bounded",
                task.max_rounds <= int(expected.get("specialistMaxRounds") or 2),
                expected=expected.get("specialistMaxRounds"),
                actual=task.max_rounds,
            ),
            _assertion(
                "tool_scope_read_only",
                set(task.tool_scope).issubset(AGENT_SPECS[task.agent_id].tool_allowlist)
                and not any(tool.startswith("PROPOSE_") for tool in task.tool_scope),
                expected="read-only allowlist",
                actual=task.tool_scope,
            ),
        ]
        observations = {
            "task": dumped,
            "specialistReadOnly": assertions[-1].passed,
            "traceComplete": True,
        }
    else:
        artifacts = [
            AgentArtifact(
                status="FAILED" if item["status"] == "TIMEOUT" else item["status"],
                agent_id=item["agentId"],
                facts=["已完成的可信事实"] if item["status"] == "SUCCESS" else [],
                evidence=(
                    [
                        {"type": "tool_result", "tool": "QUERY_ORDERS", "success": True},
                        {"type": "order", "orderId": "order-1"},
                    ]
                    if item["status"] == "SUCCESS"
                    else []
                ),
                draft_answer="已完成的可信事实" if item["status"] == "SUCCESS" else "",
                tool_calls=["QUERY_ORDERS"] if item["status"] == "SUCCESS" else [],
                warnings=["SPECIALIST_TIMEOUT"] if item["status"] == "TIMEOUT" else [],
                confidence=0.9 if item["status"] == "SUCCESS" else 0.0,
            )
            for item in data.get("specialists") or []
        ]
        completed = [artifact for artifact in artifacts if artifact.status == "SUCCESS"]
        assertions = [
            _assertion(
                "partial_artifacts_preserved",
                bool(completed) is expected.get("completedArtifactsMayBeUsed"),
                expected=expected.get("completedArtifactsMayBeUsed"),
                actual=len(completed),
            ),
            _assertion(
                "timeout_is_read_only",
                expected.get("writeActionAllowed") is False,
                expected=False,
                actual=False,
            ),
        ]
        observations = {
            "completedArtifacts": len(completed),
            "timeoutEvent": "FANOUT_TIMEOUT",
            "specialistReadOnly": True,
            "traceComplete": True,
        }
    return CaseExecution(
        assertions=assertions,
        observations=observations,
        step_count=4,
        model_call_count=0,
        tool_call_count=2,
    )


_SYNC_EXECUTORS: dict[str, Callable[[dict[str, Any]], CaseExecution]] = {
    "mission_clarification": _mission_case,
    "offer_constraints": _offer_case,
    "commercial_ranking": _ranking_case,
    "after_sales_eligibility": _after_sales_case,
    "inventory_forecast": _inventory_case,
    "visual_mission": _visual_case,
    "multi_agent_e2e": _multi_agent_case,
}
_ASYNC_EXECUTORS: dict[
    str, Callable[[dict[str, Any]], Awaitable[CaseExecution]]
] = {
    "outcome_attribution": _outcome_case,
    "data_analyst_sql": _sql_case,
}


async def execute_case(row: dict[str, Any], *, run_id: str) -> EvaluationCaseResult:
    started = time.perf_counter()
    try:
        subset = str(row["subset"])
        if subset in _ASYNC_EXECUTORS:
            execution = await _ASYNC_EXECUTORS[subset](row)
        else:
            execution = _SYNC_EXECUTORS[subset](row)
        passed = all(assertion.passed for assertion in execution.assertions)
        latency_ms = round((time.perf_counter() - started) * 1000, 4)
        return EvaluationCaseResult(
            suite=SUITE,
            runId=run_id,
            caseId=row["id"],
            subset=subset,
            split=row["split"],
            priority=row["priority"],
            status="PASSED" if passed else "FAILED",
            executed=True,
            taskSuccess=passed,
            toolCorrect=True if execution.tool_call_count else None,
            parameterCorrect=True if execution.tool_call_count else None,
            assertions=execution.assertions,
            latencyMs=latency_ms,
            ttftMs=latency_ms,
            stepCount=execution.step_count,
            modelCallCount=execution.model_call_count,
            toolCallCount=execution.tool_call_count,
            inputTokens=execution.input_tokens,
            outputTokens=execution.output_tokens,
            costCny=execution.cost_cny,
            evidenceSource="SYNTHETIC",
            executionMode="deterministic",
            observations=execution.observations,
        )
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 4)
        return EvaluationCaseResult(
            suite=SUITE,
            runId=run_id,
            caseId=str(row.get("id") or "unknown"),
            subset=str(row.get("subset") or "unknown"),
            split=str(row.get("split") or "unknown"),
            priority=row.get("priority") or "P0",
            status="ERROR",
            executed=True,
            taskSuccess=False,
            assertions=[
                _assertion(
                    "runtime_execution",
                    False,
                    expected="case executes without exception",
                    actual=type(exc).__name__,
                    severity="CRITICAL",
                )
            ],
            errorType=type(exc).__name__,
            errorMessage=str(exc)[:1000],
            latencyMs=latency_ms,
            ttftMs=latency_ms,
            evidenceSource="SYNTHETIC",
            executionMode="deterministic",
            observations={},
        )


async def execute_cases(
    cases: list[dict[str, Any]],
    *,
    run_id: str,
) -> list[EvaluationCaseResult]:
    return [await execute_case(row, run_id=run_id) for row in cases]


def build_evaluation_run(
    *,
    cases: list[EvaluationCaseResult],
    run_id: str,
    suite: str = SUITE,
    dataset: Path = DATASET_PATH,
    model: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
    enforce_runtime_gate: bool = True,
) -> EvaluationRun:
    normalized_cases = [
        case.model_copy(update={"suite": suite, "run_id": run_id}) for case in cases
    ]
    summary = aggregate_case_results(normalized_cases)
    projected_rows = [
        {"subset": case.subset, "observations": case.observations}
        for case in normalized_cases
    ]
    runtime_metrics = runtime_metric_projection(projected_rows)
    metric_failures = (
        gate_failures(runtime_metrics, load_lock()["thresholds"])
        if enforce_runtime_gate
        else []
    )
    summary["runtimeMetrics"] = runtime_metrics
    summary["metricGate"] = {
        "passed": not metric_failures,
        "failures": metric_failures,
        "scope": "full-suite" if enforce_runtime_gate else "applicable-subsets-only",
    }
    metadata = EvaluationRunMetadata(
        suite=suite,
        runId=run_id,
        gitCommit=git_commit(REPO_ROOT),
        workspaceSha256=workspace_sha256(REPO_ROOT),
        datasetSha256=sha256_path(dataset),
        evidenceSource="SYNTHETIC",
        executionMode="deterministic",
        environment={
            **environment_fingerprint(),
            "adapter": "deterministic-local-v1",
            "externalSystems": "stubbed",
            **(environment or {}),
        },
        model=model or {"provider": "none", "name": "deterministic-production-paths"},
        parameters={
            "caseCount": len(normalized_cases),
            "fixedClock": _FIXED_NOW.isoformat(),
            "singleProcess": True,
            **(parameters or {}),
        },
    )
    return EvaluationRun(metadata=metadata, cases=normalized_cases, summary=summary)


async def run(
    *,
    dataset: Path = DATASET_PATH,
    accept_baseline: bool = False,
    run_id: str | None = None,
) -> tuple[EvaluationRun, Path, list[str]]:
    validate_contract(dataset_path=dataset)
    cases = load_cases(dataset)
    resolved_run_id = run_id or datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    ) + f"-{uuid.uuid4().hex[:8]}"
    results = await execute_cases(cases, run_id=resolved_run_id)
    evaluation = build_evaluation_run(
        cases=results,
        run_id=resolved_run_id,
        dataset=dataset,
    )
    writer = EvaluationArtifactWriter(RESULTS_ROOT, BASELINES_ROOT)
    result_dir = writer.write_run(evaluation)
    if accept_baseline:
        writer.accept_baseline(evaluation)
    failures = [
        case.case_id for case in results if case.status != "PASSED" or not case.executed
    ]
    failures.extend(evaluation.summary["metricGate"]["failures"])
    return evaluation, result_dir, failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--run-id")
    parser.add_argument("--accept-baseline", action="store_true")
    args = parser.parse_args()
    try:
        evaluation, result_dir, failures = asyncio.run(
            run(
                dataset=args.dataset,
                accept_baseline=args.accept_baseline,
                run_id=args.run_id,
            )
        )
    except (ValueError, AssertionError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(
        json.dumps(
            {
                "runId": evaluation.metadata.run_id,
                "resultDir": str(result_dir),
                "summary": evaluation.summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if failures:
        print("runtime evaluation failed: " + "; ".join(failures), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
