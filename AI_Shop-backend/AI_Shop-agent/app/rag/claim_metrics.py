"""Claim-level automatic diagnostics for grounded generation."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.rag.canonical_facts import canonical_match, normalize_concept_text
from app.rag.prompt_builder import RAG_REFUSAL_TEXT

_SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+(?:[。！？!?；;\n]|$)")
_CITATION_RE = re.compile(r"\[(\d+)]")
_TRAILING_CITATION_RE = re.compile(r"([。！？!?；;])\s*((?:\[\d+]\s*)+)")

# Versioned, fact-scoped equivalents for deterministic Chinese claim scoring.
# These cover grammatical or domain-term paraphrases only. They deliberately do
# not infer a missing business fact merely because another claim cites the same
# canonical source.
_CLAIM_ALIAS_EQUIVALENTS: dict[tuple[str, str], tuple[str, ...]] = {
    ("privacy.no_external_chat_import", "不支持"): ("不会自动", "不会将"),
    ("privacy.no_external_chat_import", "永久记忆"): ("永久购物记忆",),
    ("payment.callback_idempotency_and_query", "查单"): (
        "查询支付结果",
        "查询成功结果",
        "向支付宝查询",
    ),
    ("aftersales.submit_idempotently", "不重复"): (
        "不会重复",
        "不会重复创建",
        "不会创建多个",
    ),
    ("privacy.async_delete_resume", "当前步骤"): ("可恢复状态",),
    ("checkout.current_product_revalidation", "当前价格"): ("当前sku价格",),
    ("checkout.current_product_revalidation", "重新校验"): (
        "重新读取",
        "重新计算",
    ),
    ("address.ownership_check", "归属"): ("属于当前用户", "不属于当前用户"),
    ("address.ownership_check", "校验"): ("拒绝建单",),
    ("shopping.recommendation.input_constraints", "用途"): ("使用场景",),
    ("ai.capability_and_confirmation", "写操作"): ("修改购物车",),
    ("order.cancel.by_fulfillment_state", "履约状态"): ("发货后",),
    ("payment.demo_no_real_funds", "不执行"): ("不会执行",),
}


def _attach_trailing_citations(text: str) -> str:
    """Treat ``事实。 [1]`` as a citation on the preceding fact sentence."""

    return _TRAILING_CITATION_RE.sub(r"\2\1", text)


def _aliases(claim: Mapping[str, Any]) -> list[str]:
    values = claim.get("aliases") or claim.get("concepts") or []
    if isinstance(values, str):
        values = [values]
    aliases = [str(value) for value in values if str(value).strip()]
    fact_ids = _claim_fact_ids(claim)
    for fact_id in fact_ids:
        for alias in tuple(aliases):
            aliases.extend(
                _CLAIM_ALIAS_EQUIVALENTS.get(
                    (fact_id, normalize_concept_text(alias)),
                    (),
                )
            )
    return list(dict.fromkeys(aliases))


def _claim_fact_ids(claim: Mapping[str, Any]) -> set[str]:
    values = claim.get("factIds") or claim.get("relevantFactIds") or []
    if isinstance(values, str):
        values = [values]
    return {str(value) for value in values if str(value)}


def required_claim_metrics(
    case: Mapping[str, Any],
    answer: str,
    evidence_refs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Score required claims against the answer's numbered evidence citations.

    ``evidence_refs`` is the complete ordered evidence list, not only the refs
    that happened to be cited.  Keeping the original numbering prevents a
    citation such as ``[2]`` from being incorrectly treated as ``[1]`` after
    de-duplication.
    """

    claims = [row for row in case.get("requiredClaims") or [] if isinstance(row, Mapping)]
    text = str(answer or "").strip()
    sentence_text = _attach_trailing_citations(text)
    normalized = normalize_concept_text(text)
    citation_indexes = [int(value) for value in _CITATION_RE.findall(text)]
    valid_indexes = sorted(
        {index for index in citation_indexes if 1 <= index <= len(evidence_refs)}
    )
    cited_refs = [dict(evidence_refs[index - 1]) for index in valid_indexes]
    claim_rows: list[dict[str, Any]] = []
    cited_fact_ids: set[str] = set()
    for ref in cited_refs:
        cited_fact_ids.update(canonical_match(case, ref))
    for index, claim in enumerate(claims):
        aliases = _aliases(claim)
        matched_aliases = [
            alias
            for alias in aliases
            if normalize_concept_text(alias)
            and normalize_concept_text(alias) in normalized
        ]
        matched_alias = matched_aliases[0] if matched_aliases else None
        fact_ids = _claim_fact_ids(claim)
        # Associate support with the sentence containing the claim.  This
        # catches answers that cite one correct fact for an otherwise uncited
        # second fact.
        sentence_support = False
        if matched_aliases and fact_ids:
            for alias in matched_aliases:
                for sentence in _SENTENCE_RE.findall(sentence_text):
                    if normalize_concept_text(alias) not in normalize_concept_text(
                        sentence
                    ):
                        continue
                    sentence_indexes = [
                        int(value) for value in _CITATION_RE.findall(sentence)
                    ]
                    sentence_refs = [
                        evidence_refs[index - 1]
                        for index in sentence_indexes
                        if 1 <= index <= len(evidence_refs)
                    ]
                    sentence_facts = (
                        set().union(
                            *(canonical_match(case, ref) for ref in sentence_refs)
                        )
                        if sentence_refs
                        else set()
                    )
                    sentence_support = bool(fact_ids.intersection(sentence_facts))
                    if sentence_support:
                        matched_alias = alias
                        break
                if sentence_support:
                    break
        supported = sentence_support
        required = bool(claim.get("required", claim.get("necessity", "REQUIRED") != "OPTIONAL"))
        claim_rows.append(
            {
                "claimId": str(claim.get("claimId") or f"claim-{index + 1}"),
                "required": required,
                "present": matched_alias is not None,
                "matchedAlias": matched_alias,
                "citationSupported": supported,
                "factIds": sorted(fact_ids),
            }
        )
    required_rows = [row for row in claim_rows if row["required"]]
    present_required = sum(bool(row["present"]) for row in required_rows)
    supported_required = sum(bool(row["present"] and row["citationSupported"]) for row in required_rows)
    factual_sentences = [
        sentence.strip()
        for sentence in _SENTENCE_RE.findall(sentence_text)
        if sentence.strip() and text != RAG_REFUSAL_TEXT
    ]
    unmapped = 0
    invalid_citations = 0
    for sentence in factual_sentences:
        citations = [int(value) for value in _CITATION_RE.findall(sentence)]
        invalid_citations += sum(
            value < 1 or value > len(evidence_refs) for value in citations
        )
        if not citations or all(
            value < 1 or value > len(evidence_refs) for value in citations
        ):
            unmapped += 1
    required_fact_ids = set().union(*(_claim_fact_ids(claim) for claim in claims)) if claims else set()
    return {
        "requiredClaimCount": len(required_rows),
        "presentRequiredClaimCount": present_required,
        "supportedRequiredClaimCount": supported_required,
        "requiredClaimCompleteness": present_required / len(required_rows) if required_rows else 1.0,
        "claimCitationSupport": supported_required / present_required if present_required else (1.0 if not required_rows else 0.0),
        "canonicalClaimCoverage": len(required_fact_ids.intersection(cited_fact_ids)) / len(required_fact_ids) if required_fact_ids else 1.0,
        "unmappedFactualClaimRate": unmapped / len(factual_sentences) if factual_sentences else 0.0,
        "citationGroundedFaithfulnessProxy": supported_required / len(factual_sentences) if factual_sentences else (1.0 if not required_rows else 0.0),
        "invalidCitationCount": invalid_citations,
        "claims": claim_rows,
    }


def aggregate_claim_metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    values = list(rows)
    if not values:
        return {
            "caseCount": 0,
            "requiredClaimCompleteness": 0.0,
            "claimCitationSupport": 0.0,
            "canonicalClaimCoverage": 0.0,
            "unmappedFactualClaimRate": 0.0,
            "citationGroundedFaithfulnessProxy": 0.0,
            "invalidCitationCount": 0,
        }
    fields = (
        "requiredClaimCompleteness",
        "claimCitationSupport",
        "canonicalClaimCoverage",
        "unmappedFactualClaimRate",
        "citationGroundedFaithfulnessProxy",
    )
    return {
        "caseCount": len(values),
        **{
            field: round(sum(float(row.get(field) or 0) for row in values) / len(values), 4)
            for field in fields
        },
        "invalidCitationCount": sum(int(row.get("invalidCitationCount") or 0) for row in values),
    }
