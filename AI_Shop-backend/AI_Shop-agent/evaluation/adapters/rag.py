from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

from app.config.settings import get_settings
from app.rag.embedding import embedding_evaluation_scope
from app.rag.prompt_builder import (
    build_grounding_prompt,
    deterministic_grounding_policy_fallback,
    grounding_repair_reason,
)
from app.rag.query_expander import query_expansion_evaluation_scope
from app.rag.retriever import rag_retriever, rerank_evaluation_scope
from app.services.agent_runtime import chunk_text
from app.services.llm_factory import chat_llm_config, chat_llm_for_config
from evaluation.adapters.common import assertion, provider_complete
from evaluation.core.contracts import CaseResult, CaseStatus, Domain, EvaluationCase
from evaluation.core.fault_injection import fault_point
from evaluation.core.generation import score_generation
from evaluation.core.io import utc_now
from evaluation.core.semantic_judge import run_semantic_shadow_judge
from evaluation.core.usage import merge_usage, normalize_usage


class RagGenerationError(RuntimeError):
    """A bounded answer-generation failure with its attempted-call ledger."""

    def __init__(self, cause: Exception, facts: dict[str, Any]):
        self.cause = cause
        self.facts = facts
        super().__init__(str(cause))


_GENERATION_UNAVAILABLE_ANSWER = (
    "当前回答生成服务暂时不可用，无法基于已检索证据给出可靠结论。"
    "请稍后重试或联系人工客服。"
)


def _ref_facts(ref: Mapping[str, Any]) -> set[str]:
    values = ref.get("factIds") or []
    if isinstance(values, str):
        values = [values]
    return {str(value) for value in values if str(value)}


def _retrieval_metrics(
    candidate_refs: Sequence[Mapping[str, Any]],
    relevant_fact_ids: Sequence[str],
) -> dict[str, float]:
    relevant = {str(value) for value in relevant_fact_ids}
    if not relevant:
        return {}

    def recall(k: int) -> float:
        found: set[str] = set()
        for ref in candidate_refs[:k]:
            found.update(relevant.intersection(_ref_facts(ref)))
        return len(found) / len(relevant)

    reciprocal_rank = 0.0
    for rank, ref in enumerate(candidate_refs[:10], 1):
        if relevant.intersection(_ref_facts(ref)):
            reciprocal_rank = 1 / rank
            break

    import math

    grades = [len(relevant.intersection(_ref_facts(ref))) for ref in candidate_refs]

    def dcg(values: Sequence[int]) -> float:
        return sum(
            (2**grade - 1) / math.log2(rank + 1)
            for rank, grade in enumerate(values[:5], 1)
        )

    # NDCG compares the observed ranking with the ideal ordering of the same
    # graded candidates. A chunk can support multiple facts, so the ideal gains
    # must use those exact grades instead of pretending every fact is a separate
    # binary-relevance document.
    ideal = dcg(sorted(grades, reverse=True))
    ndcg = dcg(grades) / ideal if ideal else 0.0
    return {
        "retrievalRecallAt3": recall(3),
        "retrievalRecallAt5": recall(5),
        "retrievalMrrAt10": reciprocal_rank,
        "retrievalNdcgAt5": ndcg,
    }


def _usage(response: Any, *, default_model: str | None = None) -> dict[str, Any]:
    metadata = getattr(response, "response_metadata", None) or {}
    usage = getattr(response, "usage_metadata", None) or {}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if input_tokens is None or output_tokens is None:
        token_usage = metadata.get("token_usage") or {}
        input_tokens = token_usage.get("prompt_tokens")
        output_tokens = token_usage.get("completion_tokens")
    model = str(metadata.get("model_name") or default_model or get_settings().llm_model)
    pricing = get_settings().llm_pricing_cny_per_million_json.get(model) or {}
    return normalize_usage(
        {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "providerCalls": 1,
        },
        pricing=pricing if pricing else None,
        provider=str(getattr(get_settings(), "llm_provider", "openai-compatible")),
        model=model,
        default_calls=1,
    )


def _evaluation_llm(
    *, timeout_seconds: float | None = None, model: str | None = None
):
    """Build a bounded, non-streaming client for evaluation-only calls.

    Production chat remains streaming.  Evaluation needs a finite provider
    boundary so a stalled SSE response cannot prevent evidence from being
    written; the endpoint, credentials and model are still the configured real
    provider values.
    """

    configured_timeout = timeout_seconds or float(
        os.getenv("AI_EVAL_RAG_LLM_TIMEOUT_SECONDS", str(get_settings().llm_timeout))
    )
    timeout = max(1, int(configured_timeout))
    config = chat_llm_config(disable_thinking=True, streaming=False)._replace(
        model=str(model or get_settings().llm_model),
        timeout=timeout,
        max_retries=0,
    )
    return chat_llm_for_config(config)


def _retryable_generation_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if type(exc).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "RateLimitError",
    }:
        return True
    status_code = getattr(exc, "status_code", None)
    return isinstance(status_code, int) and (status_code == 429 or status_code >= 500)


def _missing_generation_usage() -> dict[str, Any]:
    settings = get_settings()
    return normalize_usage(
        None,
        provider=str(getattr(settings, "llm_provider", "openai-compatible")),
        model=str(settings.llm_model),
        default_calls=1,
    )


async def _generate(
    query: str,
    retrieval: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    llm = _evaluation_llm()
    evidence_items = list(retrieval.get("evidenceItems") or [])
    evidence_state = str(retrieval.get("evidenceState") or "INSUFFICIENT")
    prompt = build_grounding_prompt(
        query,
        evidence_state=evidence_state,
        evidence_items=evidence_items,
    )
    calls: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    successes = 0
    retries = 0
    repair_attempted = False
    deterministic_fallback: dict[str, Any] | None = None
    repair_remaining: str | None = None
    repair_error: RagGenerationError | None = None
    max_retries = max(0, min(3, int(os.getenv("AI_EVAL_RAG_LLM_RETRIES", "1"))))
    retry_backoff = max(
        0.0,
        min(5.0, float(os.getenv("AI_EVAL_RAG_LLM_RETRY_BACKOFF_SECONDS", "0.25"))),
    )

    async def invoke(messages: Any) -> Any:
        nonlocal retries, successes
        for attempt in range(max_retries + 1):
            try:
                fault_point("llm")
                response = await llm.ainvoke(messages)
            except Exception as exc:
                retryable = _retryable_generation_error(exc)
                calls.append(_missing_generation_usage())
                failures.append(
                    {
                        "attempt": len(calls),
                        "type": type(exc).__name__,
                        "retryable": retryable,
                    }
                )
                if attempt >= max_retries or not retryable:
                    usage = merge_usage(calls)
                    usage["retryCount"] = retries
                    raise RagGenerationError(
                        exc,
                        {
                            "requests": len(calls),
                            "successes": successes,
                            "failures": len(failures),
                            "terminalSuccess": False,
                            "retries": retries,
                            "failureAttempts": failures,
                            "calls": calls,
                            "usage": usage,
                        },
                    ) from exc
                retries += 1
                if retry_backoff:
                    await asyncio.sleep(retry_backoff * (2**attempt))
                continue
            calls.append(_usage(response))
            successes += 1
            return response
        raise AssertionError("bounded generation retry loop exhausted unexpectedly")

    response = await invoke(prompt.messages())
    answer = chunk_text(getattr(response, "content", "") or "").strip()
    reason = grounding_repair_reason(
        answer,
        evidence_state=evidence_state,
        evidence_count=len(evidence_items),
        evidence_items=evidence_items,
        query=query,
    )
    if reason:
        repair_attempted = True
        repair = build_grounding_prompt(
            query,
            evidence_state=evidence_state,
            evidence_items=evidence_items,
            repair_reason=reason,
        )
        try:
            response = await invoke(repair.messages())
        except RagGenerationError as exc:
            repair_error = exc
            repair_remaining = f"repair provider failed: {type(exc.cause).__name__}"
            reason = f"{reason}; remaining={repair_remaining}"
        else:
            repaired = chunk_text(getattr(response, "content", "") or "").strip()
            remaining = grounding_repair_reason(
                repaired,
                evidence_state=evidence_state,
                evidence_count=len(evidence_items),
                evidence_items=evidence_items,
                query=query,
            )
            answer = repaired
            repair_remaining = remaining
            reason = f"{reason}; remaining={remaining}" if remaining else reason
        if repair_remaining:
            deterministic_fallback = deterministic_grounding_policy_fallback(
                query,
                evidence_state=evidence_state,
                evidence_items=evidence_items,
            )
            if deterministic_fallback:
                answer = str(deterministic_fallback["answer"])
                reason = (
                    f"{reason}; deterministicFallback="
                    f"{deterministic_fallback['event']}"
                )
        if repair_error is not None and deterministic_fallback is None:
            raise repair_error
    usage = merge_usage(calls)
    usage["retryCount"] = retries
    return answer, {
        "requests": len(calls),
        "successes": successes,
        "failures": len(failures),
        "terminalSuccess": True,
        "retries": retries,
        "failureAttempts": failures,
        "calls": calls,
        "boundedRepairAttempted": repair_attempted,
        "repairReason": reason,
        "deterministicFallbackUsed": deterministic_fallback is not None,
        "deterministicFallback": deterministic_fallback,
        "repairRemaining": repair_remaining,
        "inputTokens": usage["inputTokens"],
        "outputTokens": usage["outputTokens"],
        "costCny": usage["costCny"],
        "costStatus": usage["costStatus"],
        "usage": usage,
    }


async def _semantic_shadow(
    *,
    answer: str,
    expected: Mapping[str, Any],
    retrieval: Mapping[str, Any],
    lexical_details: Mapping[str, Any],
) -> dict[str, Any]:
    claims = [dict(item) for item in expected.get("requiredClaims") or [] if isinstance(item, Mapping)]
    if not claims:
        claims = [
            {
                "claimId": "no-answer-policy",
                "description": "The answer should abstain when the supplied evidence is insufficient.",
            }
        ]
    evidence = [
        dict(item)
        for item in retrieval.get("evidenceItems") or retrieval.get("source_refs") or []
        if isinstance(item, Mapping)
    ]
    lexical_labels = {
        str(item.get("claimId")): (
            "SUPPORTED"
            if bool(item.get("present")) and bool(item.get("citationSupported"))
            else "UNSUPPORTED"
        )
        for item in lexical_details.get("claims") or []
        if isinstance(item, Mapping)
    }
    if "no-answer-policy" in {str(item.get("claimId")) for item in claims}:
        lexical_labels["no-answer-policy"] = (
            "SUPPORTED" if bool(expected.get("noAnswer")) else "UNSUPPORTED"
        )
    # The production chat client is streaming.  The shadow judge deliberately
    # gets a separate non-streaming client so a stalled SSE stream cannot hold
    # the evaluation runner past its bounded judge timeout.
    judge_model = str(
        os.getenv("AI_EVAL_SEMANTIC_JUDGE_MODEL", "").strip()
        or get_settings().llm_fallback_model.strip()
        or get_settings().llm_model
    )
    judge_timeout = float(os.getenv("AI_EVAL_SEMANTIC_JUDGE_TIMEOUT_SECONDS", "30"))
    judge = _evaluation_llm(timeout_seconds=judge_timeout, model=judge_model)
    judge_usage: list[dict[str, Any]] = []

    async def invoke(prompt: str) -> Any:
        fault_point("llm")
        response = await judge.ainvoke(prompt)
        judge_usage.append(_usage(response, default_model=judge_model))
        return response

    result = await run_semantic_shadow_judge(
        answer=answer,
        claims=claims,
        evidence=evidence,
        invoke=invoke,
        provider=str(getattr(get_settings(), "llm_provider", "openai-compatible")),
        model=judge_model,
        # Shadow diagnostics have a deliberately small independent budget. A
        # slow judge must never multiply the latency of the quality run; its
        # unavailable status is retained in evidence instead.
        timeout_seconds=judge_timeout,
        retries=int(os.getenv("AI_EVAL_SEMANTIC_JUDGE_RETRIES", "1")),
        lexical_labels=lexical_labels,
    )
    result["usage"] = merge_usage(judge_usage)
    return result


async def run_rag_case(case: EvaluationCase) -> CaseResult:
    started_at = utc_now()
    started = time.perf_counter()
    query = str(case.input["query"])
    llm_facts: dict[str, Any]
    with (
        embedding_evaluation_scope(bypass_cache=True) as embedding_stats,
        rerank_evaluation_scope() as rerank_stats,
        query_expansion_evaluation_scope() as expansion_stats,
    ):
        retrieval = await rag_retriever.search_faq_with_trace(
            query,
            include_evaluation_candidates=True,
        )
        try:
            answer, llm_facts = await _generate(query, retrieval)
        except RagGenerationError as exc:
            llm_facts = exc.facts
            facts = {
                "embedding": embedding_stats.snapshot(),
                "rerank": rerank_stats.snapshot(),
                "llm": llm_facts,
            }
            _complete, provider_facts = provider_complete(case.required_providers, facts)
            return CaseResult(
                case_id=case.case_id,
                domain=Domain.RAG,
                # The ordinary quality case still fails closed because its
                # required provider and generation contract did not complete.
                # A structured degraded response remains available for fault
                # recovery assessment without relabeling the provider failure.
                status=CaseStatus.FAILED,
                metrics={
                    "providerCompleteness": 0,
                    "generationCorrectness": 0,
                    "invalidCitationCount": 0,
                    "severeSafetyViolationCount": 0,
                    "unsafeAnswerCount": 0,
                    "constraintViolationCount": 0,
                    "hardConstraintBypassCount": 0,
                },
                latency_ms=(time.perf_counter() - started) * 1000,
                output={
                    "query": query,
                    "answer": _GENERATION_UNAVAILABLE_ANSWER,
                    "evidenceState": retrieval.get("evidenceState"),
                    "sourceRefs": list(retrieval.get("source_refs") or []),
                    "trace": retrieval.get("trace") or {},
                    "generationAttempts": llm_facts,
                    "terminalState": "DEGRADED",
                    "terminalStateReason": "LLM_GENERATION_UNAVAILABLE",
                    "fallbackUsed": False,
                    "degraded": True,
                    "responseEmitted": True,
                },
                providers=provider_facts,
                assertions=[
                    assertion("provider-complete", False, provider_facts),
                    assertion(
                        "safe-degraded-response",
                        True,
                        {
                            "terminalState": "DEGRADED",
                            "answer": _GENERATION_UNAVAILABLE_ANSWER,
                        },
                    ),
                ],
                error={"type": type(exc.cause).__name__, "message": str(exc.cause)[:500]},
                started_at=started_at,
                completed_at=utc_now(),
                usage=llm_facts["usage"],
                slice=case.slice_tags[0] if case.slice_tags else None,
            )
    latency_ms = (time.perf_counter() - started) * 1000
    candidate_refs = list(
        retrieval.get("_evaluationCandidateRefs") or retrieval.get("source_refs") or []
    )
    source_refs = list(retrieval.get("source_refs") or [])
    relevant = [str(value) for value in case.expected.get("relevantFactIds") or []]
    metrics: dict[str, float | int] = _retrieval_metrics(candidate_refs, relevant)
    relevant_set = set(relevant)
    selected_relevant = [ref for ref in source_refs if relevant_set.intersection(_ref_facts(ref))]
    if relevant:
        covered = (
            set().union(*(relevant_set.intersection(_ref_facts(ref)) for ref in source_refs))
            if source_refs
            else set()
        )
        metrics["sourcePrecision"] = (
            len(selected_relevant) / len(source_refs) if source_refs else 0.0
        )
        metrics["sourceCoverage"] = len(covered) / len(relevant_set)
    generation_metrics, generation_details = score_generation(
        case.expected,
        answer=answer,
        refs=source_refs,
        evidence_state=str(retrieval.get("evidenceState") or "INSUFFICIENT"),
    )
    metrics.update(generation_metrics)
    semantic_judgment = await _semantic_shadow(
        answer=answer,
        expected=case.expected,
        retrieval=retrieval,
        lexical_details=generation_details,
    )
    expansion_snapshot = expansion_stats.snapshot()
    # Query expansion is an optional recall enhancement with a deterministic
    # fallback. Keep its provider outcome visible, but do not merge it into the
    # core answer-generation ledger: an expansion timeout must not make a
    # grounded answer look as if its required LLM generation failed.
    llm_facts = {
        **llm_facts,
        "queryExpansion": expansion_snapshot,
        "optionalProviderFailures": int(expansion_snapshot.get("providerFailures") or 0),
    }
    facts = {
        "embedding": embedding_stats.snapshot(),
        "rerank": rerank_stats.snapshot(),
        "llm": llm_facts,
    }
    effective_required = set(case.required_providers)
    trace_mode = str((retrieval.get("trace") or {}).get("mode") or "")
    if trace_mode == "exact" or (
        str(retrieval.get("evidenceState") or "") == "QUARANTINED"
        and not candidate_refs
    ):
        # Exact FAQ and quarantined input are deliberate short paths. Missing
        # retrieval-provider calls here are N/A, not silent fallback.
        for provider in ("embedding", "rerank"):
            snapshot = facts[provider]
            if (
                int(snapshot.get("providerRequests") or 0) == 0
                and int(snapshot.get("providerFailures") or 0) == 0
            ):
                snapshot["notApplicable"] = True
                snapshot["notApplicableReason"] = f"rag_{trace_mode or 'quarantine'}_path"
    if int(facts["embedding"].get("providerRequests") or 0) > 0:
        effective_required.add("embedding")
    if int(facts["rerank"].get("eligibleRequests") or 0) > 0:
        effective_required.add("rerank")
    complete, provider_facts = provider_complete(sorted(effective_required), facts)
    metrics["providerCompleteness"] = complete
    metrics["queryExpansionFailureCount"] = int(
        expansion_snapshot.get("providerFailures") or 0
    )
    assertions = [
        assertion("provider-complete", complete == 1, provider_facts),
        assertion(
            "generation-contract",
            int(metrics["generationCorrectness"]) == 1,
            generation_details,
        ),
        assertion(
            "no-invalid-citations",
            int(metrics["invalidCitationCount"]) == 0,
            generation_details["invalidCitationIndexes"],
        ),
        assertion(
            "no-severe-safety-violation",
            int(metrics["severeSafetyViolationCount"]) == 0,
            generation_details["leakMarkers"],
        ),
    ]
    passed = all(row["passed"] for row in assertions)
    return CaseResult(
        case_id=case.case_id,
        domain=Domain.RAG,
        status=CaseStatus.PASSED if passed else CaseStatus.FAILED,
        metrics=metrics,
        latency_ms=latency_ms,
        output={
            "query": query,
            "answer": answer,
            "evidenceState": retrieval.get("evidenceState"),
            "sourceRefs": source_refs,
            "candidateRefs": candidate_refs,
            "trace": retrieval.get("trace") or {},
            "queryPlan": retrieval.get("queryPlan"),
            "securityFlags": retrieval.get("securityFlags") or [],
            "generation": generation_details,
            "queryExpansion": expansion_snapshot,
            "usage": llm_facts.get("usage") or normalize_usage(None, default_calls=1),
            "semanticShadow": semantic_judgment,
        },
        providers=provider_facts,
        assertions=assertions,
        started_at=started_at,
        completed_at=utc_now(),
        usage=merge_usage(
            [
                llm_facts.get("usage") or normalize_usage(None, default_calls=1),
                semantic_judgment.get("usage") or normalize_usage(None),
            ]
        ),
        slice=case.slice_tags[0] if case.slice_tags else None,
        semantic_judgment=semantic_judgment,
    )
