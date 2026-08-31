"""Aggregate normalized public benchmarks without entering canonical evaluation splits."""

from __future__ import annotations

import argparse
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from evaluation.core.io import (
    atomic_write_json,
    atomic_write_text,
    canonical_json_bytes,
    load_json,
    load_jsonl,
    sha256_bytes,
    sha256_file,
)
from evaluation.core.metrics import (
    bootstrap_interval,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank_at_k,
    wilson_interval,
)

MANIFEST_SCHEMA_VERSION = "aishop-public-transfer-input/v1"
REPORT_SCHEMA_VERSION = "aishop-public-transfer-report/v2"
ARTIFACT_MANIFEST_SCHEMA_VERSION = "aishop-public-transfer-artifacts/v2"
SCORER_VERSION = "aishop-public-transfer-scorer/v2"
_SUPPORTED_SCORER_VERSIONS = {
    "aishop-public-transfer-scorer/v1",
    SCORER_VERSION,
}
BOOTSTRAP_SAMPLES = 1_000
BOOTSTRAP_SEED = 20260831

GOVERNANCE = {
    "evidenceRole": "PUBLIC_TRANSFER_DIAGNOSTIC",
    "finalUnseen": False,
    "releaseGateEligible": False,
    "postHoc": True,
    "productionSlo": False,
    "canonicalSplitEligible": False,
}

_MANIFEST_FIELDS = {
    "schemaVersion",
    "datasetId",
    "officialUrl",
    "license",
    "upstreamRevisionOrCommit",
    "perFileInventoryOrCanonicalInventorySha256",
    "selectionPolicy",
    "scorerVersion",
    "modelAndPromptFingerprintOrNOT_APPLICABLE",
    "caseCountAndEligibleDenominators",
    "normalizedInputSha256",
    "exhaustiveClaimGold",
    "exhaustiveCitationGold",
    "officialAgentExecution",
    *GOVERNANCE,
}
_DENOMINATOR_FIELDS = {
    "caseCount",
    "rankingCaseEligible",
    "claimOrSpanCaseEligible",
    "agentTrialEligible",
    "agentCaseEligible",
}
_OPTIONAL_DENOMINATOR_FIELDS = {
    "gradedRankingCaseEligible",
    "binaryRankingCaseEligible",
}
_COMMON_ROW_FIELDS = {"kind", "caseKey", "slice"}
_SAFE_SUCCESS_FIELDS = {
    "state_oracle_eligible",
    "goal_state_match",
    "terminal_state_correct",
    "policy_and_tool_trace_pass",
    "authoritative_object_field_value_match",
    "confirmation_timeline_pass",
    "no_forbidden_or_duplicate_side_effect",
}
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FORBIDDEN_OUTPUT_FIELDS = {"query", "answer", "snippet", "comment", "reason", "caseId"}
_OUTPUT_FILES = {"report.json", "manifest.json", "SHA256SUMS"}


class PublicTransferError(ValueError):
    """Raised when public-transfer input cannot be aggregated safely."""


def _exact_fields(
    value: Mapping[str, Any],
    required: set[str],
    *,
    label: str,
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise PublicTransferError(f"{label} is missing fields: {sorted(missing)}")
    if unknown:
        raise PublicTransferError(f"{label} contains unknown fields: {sorted(unknown)}")


def _text(value: Any, *, field: str, maximum: int = 10_000) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise PublicTransferError(f"{field} must be a non-empty bounded string")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise PublicTransferError(f"{field} must not contain control characters")
    return value


def _safe_id(value: Any, *, field: str) -> str:
    text = _text(value, field=field, maximum=128)
    if _SAFE_ID.fullmatch(text) is None:
        raise PublicTransferError(f"{field} must be a safe identifier")
    return text


def _hash(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise PublicTransferError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _boolean(value: Any, *, field: str) -> bool:
    if type(value) is not bool:
        raise PublicTransferError(f"{field} must be boolean")
    return value


def _integer(value: Any, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise PublicTransferError(f"{field} must be an integer >= {minimum}")
    return value


def _validate_manifest(value: Any, *, input_path: Path | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PublicTransferError("manifest must be an object")
    _exact_fields(value, _MANIFEST_FIELDS, label="manifest")
    if value["schemaVersion"] != MANIFEST_SCHEMA_VERSION:
        raise PublicTransferError("unsupported manifest schemaVersion")
    if value["scorerVersion"] not in _SUPPORTED_SCORER_VERSIONS:
        raise PublicTransferError("unsupported scorerVersion")
    _safe_id(value["datasetId"], field="datasetId")
    official_url = _text(value["officialUrl"], field="officialUrl", maximum=2_048)
    parsed = urlsplit(official_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PublicTransferError("officialUrl must be a public HTTP(S) URL without credentials")
    _text(value["license"], field="license", maximum=256)
    _text(value["upstreamRevisionOrCommit"], field="upstreamRevisionOrCommit", maximum=256)
    _hash(
        value["perFileInventoryOrCanonicalInventorySha256"],
        field="perFileInventoryOrCanonicalInventorySha256",
    )
    _text(value["selectionPolicy"], field="selectionPolicy", maximum=2_048)
    fingerprint = value["modelAndPromptFingerprintOrNOT_APPLICABLE"]
    if fingerprint != "NOT_APPLICABLE":
        _hash(fingerprint, field="modelAndPromptFingerprintOrNOT_APPLICABLE")
    expected_input_hash = _hash(value["normalizedInputSha256"], field="normalizedInputSha256")
    if input_path is not None and sha256_file(input_path) != expected_input_hash:
        raise PublicTransferError("normalized input SHA-256 does not match manifest")
    for field in ("exhaustiveClaimGold", "exhaustiveCitationGold", "officialAgentExecution"):
        _boolean(value[field], field=field)
    for field, expected in GOVERNANCE.items():
        if value[field] != expected or type(value[field]) is not type(expected):
            raise PublicTransferError(f"manifest governance field {field} is invalid")
    denominators = value["caseCountAndEligibleDenominators"]
    if not isinstance(denominators, dict):
        raise PublicTransferError("caseCountAndEligibleDenominators must be an object")
    _exact_fields(
        denominators,
        _DENOMINATOR_FIELDS,
        label="denominators",
        optional=_OPTIONAL_DENOMINATOR_FIELDS,
    )
    for field in _DENOMINATOR_FIELDS | _OPTIONAL_DENOMINATOR_FIELDS.intersection(denominators):
        _integer(denominators[field], field=f"denominators.{field}")
    return value


def _validate_spans(value: Any, *, field: str) -> list[tuple[int, int]]:
    if not isinstance(value, list):
        raise PublicTransferError(f"{field} must be a list")
    spans: list[tuple[int, int]] = []
    for span in value:
        if not isinstance(span, list) or len(span) != 2:
            raise PublicTransferError(f"{field} entries must be [start, end] pairs")
        start = _integer(span[0], field=f"{field}.start")
        end = _integer(span[1], field=f"{field}.end", minimum=1)
        if start >= end or end > 10_000_000:
            raise PublicTransferError(f"{field} entries must be bounded half-open spans")
        spans.append((start, end))
    return spans


def _validate_row(row: dict[str, Any]) -> None:
    kind = row.get("kind")
    if kind == "ranking_case":
        expected = _COMMON_ROW_FIELDS | {"ranking", "qrels"}
    elif kind == "claim_or_span_case":
        task = row.get("task")
        task_fields = {
            "binary_classification": {"goldPositive", "predictedPositive"},
            "span_detection": {"goldSpans", "predictedSpans"},
            "exact_or_alias": {"prediction", "goldAnswers"},
            "answer_groups": {"prediction", "goldAnswerGroups"},
        }.get(task)
        if task_fields is None:
            raise PublicTransferError("claim_or_span_case has an unsupported task")
        expected = _COMMON_ROW_FIELDS | {"task"} | task_fields
    elif kind == "agent_trial":
        expected = _COMMON_ROW_FIELDS | {"trialNumber", "safe_success"}
    else:
        raise PublicTransferError("unsupported normalized row kind")
    _exact_fields(
        row,
        expected,
        label=f"{kind or 'row'} row",
        optional={"relevanceThreshold"} if kind == "ranking_case" else None,
    )
    _text(row["caseKey"], field="caseKey", maximum=512)
    _safe_id(row["slice"], field="slice")

    if kind == "ranking_case":
        ranking = row["ranking"]
        qrels = row["qrels"]
        if not isinstance(ranking, list) or any(
            not isinstance(item, str) or not item or len(item) > 512 for item in ranking
        ):
            raise PublicTransferError("ranking must be a list of bounded document identifiers")
        if len(set(ranking)) != len(ranking):
            raise PublicTransferError("ranking must not contain duplicate identifiers")
        if not isinstance(qrels, dict) or not qrels:
            raise PublicTransferError("qrels must be a non-empty object")
        for document, grade in qrels.items():
            if not isinstance(document, str) or not document or len(document) > 512:
                raise PublicTransferError("qrels keys must be bounded document identifiers")
            if _integer(grade, field="qrels grade") > 100:
                raise PublicTransferError("qrels grades must be <= 100")
        _integer(row.get("relevanceThreshold", 1), field="relevanceThreshold", minimum=1)
    elif kind == "claim_or_span_case":
        if row["task"] == "binary_classification":
            _boolean(row["goldPositive"], field="goldPositive")
            _boolean(row["predictedPositive"], field="predictedPositive")
        elif row["task"] == "span_detection":
            _validate_spans(row["goldSpans"], field="goldSpans")
            _validate_spans(row["predictedSpans"], field="predictedSpans")
        elif row["task"] == "exact_or_alias":
            if not isinstance(row["prediction"], str) or len(row["prediction"]) > 10_000:
                raise PublicTransferError("prediction must be a bounded string")
            answers = row["goldAnswers"]
            if (
                not isinstance(answers, list)
                or not answers
                or any(
                    not isinstance(answer, str) or not answer or len(answer) > 10_000
                    for answer in answers
                )
            ):
                raise PublicTransferError("goldAnswers must be a non-empty list of bounded strings")
        else:
            if not isinstance(row["prediction"], str) or len(row["prediction"]) > 10_000:
                raise PublicTransferError("prediction must be a bounded string")
            groups = row["goldAnswerGroups"]
            if (
                not isinstance(groups, list)
                or not groups
                or any(
                    not isinstance(group, list)
                    or not group
                    or any(
                        not isinstance(answer, str) or not answer or len(answer) > 10_000
                        for answer in group
                    )
                    for group in groups
                )
            ):
                raise PublicTransferError(
                    "goldAnswerGroups must be a non-empty list of non-empty alias lists"
                )
    else:
        _integer(row["trialNumber"], field="trialNumber", minimum=1)
        safe_success = row["safe_success"]
        if not isinstance(safe_success, dict):
            raise PublicTransferError("safe_success must be an object")
        _exact_fields(safe_success, _SAFE_SUCCESS_FIELDS, label="safe_success")
        for field in _SAFE_SUCCESS_FIELDS:
            _boolean(safe_success[field], field=f"safe_success.{field}")


def _actual_denominators(rows: Sequence[dict[str, Any]], *, official_agent: bool) -> dict[str, int]:
    agent_rows = [row for row in rows if row["kind"] == "agent_trial"]
    ranking_rows = [row for row in rows if row["kind"] == "ranking_case"]
    return {
        "caseCount": len({(row["kind"], row["caseKey"]) for row in rows}),
        "rankingCaseEligible": len(ranking_rows),
        "gradedRankingCaseEligible": sum(
            any(grade > 0 for grade in row["qrels"].values()) for row in ranking_rows
        ),
        "binaryRankingCaseEligible": sum(
            any(grade >= row.get("relevanceThreshold", 1) for grade in row["qrels"].values())
            for row in ranking_rows
        ),
        "claimOrSpanCaseEligible": sum(row["kind"] == "claim_or_span_case" for row in rows),
        "agentTrialEligible": len(agent_rows) if official_agent else 0,
        "agentCaseEligible": len({row["caseKey"] for row in agent_rows}) if official_agent else 0,
    }


def _validate_rows(rows: list[dict[str, Any]], manifest: Mapping[str, Any]) -> None:
    seen_ranking: set[str] = set()
    seen_claim: set[tuple[str, str]] = set()
    seen_trials: set[tuple[str, int]] = set()
    for row in rows:
        _validate_row(row)
        if row["kind"] == "ranking_case":
            key: Any = row["caseKey"]
            seen = seen_ranking
        elif row["kind"] == "claim_or_span_case":
            key = (row["caseKey"], row["task"])
            seen = seen_claim
        else:
            key = (row["caseKey"], row["trialNumber"])
            seen = seen_trials
        if key in seen:
            raise PublicTransferError("normalized input contains a duplicate case/trial key")
        seen.add(key)
    actual = _actual_denominators(rows, official_agent=manifest["officialAgentExecution"])
    for field in _OPTIONAL_DENOMINATOR_FIELDS - set(manifest["caseCountAndEligibleDenominators"]):
        actual.pop(field)
    if actual != manifest["caseCountAndEligibleDenominators"]:
        raise PublicTransferError("manifest denominators do not match normalized input")


def _round(value: float) -> float:
    return round(float(value), 6)


def _seed(name: str) -> int:
    return BOOTSTRAP_SEED ^ int(sha256_bytes(name.encode("utf-8"))[:8], 16)


def _unavailable(*, denominator: int, note: str) -> dict[str, Any]:
    return {
        "status": "UNAVAILABLE",
        "denominator": denominator,
        "noteCodes": [note],
    }


def _wilson_metric(successes: int, total: int) -> dict[str, Any]:
    if total == 0:
        return _unavailable(denominator=0, note="NO_ELIGIBLE_CASES")
    lower, upper = wilson_interval(successes, total)
    return {
        "status": "AVAILABLE",
        "value": _round(successes / total),
        "denominator": total,
        "interval": {
            "method": "wilson",
            "confidenceLevel": 0.95,
            "lower": _round(lower),
            "upper": _round(upper),
        },
    }


def _mean_metric(values: Sequence[float], *, name: str) -> dict[str, Any]:
    if not values:
        return _unavailable(denominator=0, note="NO_ELIGIBLE_CASES")
    rows = [float(value) for value in values]
    lower, upper = bootstrap_interval(
        rows,
        lambda sample: sum(sample) / len(sample),
        samples=BOOTSTRAP_SAMPLES,
        seed=_seed(name),
    )
    return {
        "status": "AVAILABLE",
        "value": _round(sum(rows) / len(rows)),
        "denominator": len(rows),
        "interval": {
            "method": "case-bootstrap",
            "confidenceLevel": 0.95,
            "samples": BOOTSTRAP_SAMPLES,
            "lower": _round(lower),
            "upper": _round(upper),
        },
    }


def _ranking_metrics(
    cases: Sequence[dict[str, Any]], *, dataset: str, scope: str
) -> dict[str, Any]:
    values: dict[str, list[float]] = defaultdict(list)
    for name in (
        "ceilingNormalizedRecallAt5",
        "ceilingNormalizedRecallAt10",
        "hitAt5",
        "hitAt10",
        "hitAt100",
        "mrrAt10",
        "ndcgAt5",
        "ndcgAt10",
        "precisionAt1",
        "recallAt5",
        "recallAt10",
        "recallCeilingAt5",
        "recallCeilingAt10",
        "unjudgedAt5",
        "unjudgedAt10",
    ):
        values[name]
    for case in cases:
        ranking, qrels = case["ranking"], case["qrels"]
        threshold = case.get("relevanceThreshold", 1)
        binary_qrels = {document: int(grade >= threshold) for document, grade in qrels.items()}
        if any(grade > 0 for grade in qrels.values()):
            values["ndcgAt5"].append(ndcg_at_k(ranking, qrels, 5))
            values["ndcgAt10"].append(ndcg_at_k(ranking, qrels, 10))
        for k in (5, 10):
            returned = ranking[:k]
            values[f"unjudgedAt{k}"].append(
                sum(document not in qrels for document in returned) / len(returned)
                if returned
                else 0.0
            )
        positive_count = sum(binary_qrels.values())
        if not positive_count:
            continue
        values["precisionAt1"].append(reciprocal_rank_at_k(ranking, binary_qrels, 1))
        values["mrrAt10"].append(reciprocal_rank_at_k(ranking, binary_qrels, 10))
        for k in (5, 10, 100):
            hit = float(any(binary_qrels.get(document, 0) for document in ranking[:k]))
            values[f"hitAt{k}"].append(hit)
        for k in (5, 10):
            recall = recall_at_k(ranking, binary_qrels, k)
            ceiling = min(k, positive_count) / positive_count
            values[f"recallAt{k}"].append(recall)
            values[f"recallCeilingAt{k}"].append(ceiling)
            values[f"ceilingNormalizedRecallAt{k}"].append(recall / ceiling)
    return {
        name: (
            _wilson_metric(round(sum(metric_values)), len(metric_values))
            if name == "precisionAt1" or name.startswith("hitAt")
            else _mean_metric(metric_values, name=f"{dataset}:ranking:{scope}:{name}")
        )
        for name, metric_values in sorted(values.items())
    }


def _ranking_report(rows: Sequence[dict[str, Any]], *, dataset: str) -> dict[str, Any]:
    all_cases = [row for row in rows if row["kind"] == "ranking_case"]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_cases:
        grouped[row["slice"]].append(row)
    slices = []
    for slice_name, cases in sorted(grouped.items()):
        graded_cases = sum(any(grade > 0 for grade in case["qrels"].values()) for case in cases)
        binary_cases = sum(
            any(grade >= case.get("relevanceThreshold", 1) for grade in case["qrels"].values())
            for case in cases
        )
        slices.append(
            {
                "slice": slice_name,
                "caseDenominator": len(cases),
                "gradedCaseDenominator": graded_cases,
                "binaryCaseDenominator": binary_cases,
                "metrics": _ranking_metrics(cases, dataset=dataset, scope=slice_name),
            }
        )
    graded_cases = sum(any(grade > 0 for grade in case["qrels"].values()) for case in all_cases)
    binary_cases = sum(
        any(grade >= case.get("relevanceThreshold", 1) for grade in case["qrels"].values())
        for case in all_cases
    )
    return {
        "status": "RUN" if slices else "NOT_RUN",
        "caseDenominator": len(all_cases),
        "gradedCaseDenominator": graded_cases,
        "binaryCaseDenominator": binary_cases,
        "metrics": _ranking_metrics(all_cases, dataset=dataset, scope="all"),
        "sliceResults": slices,
    }


def _merge_spans(spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _span_counts(
    gold: Sequence[tuple[int, int]], predicted: Sequence[tuple[int, int]]
) -> tuple[int, int, int]:
    gold_merged, predicted_merged = _merge_spans(gold), _merge_spans(predicted)
    gold_size = sum(end - start for start, end in gold_merged)
    predicted_size = sum(end - start for start, end in predicted_merged)
    overlap = 0
    gold_index = predicted_index = 0
    while gold_index < len(gold_merged) and predicted_index < len(predicted_merged):
        gold_start, gold_end = gold_merged[gold_index]
        predicted_start, predicted_end = predicted_merged[predicted_index]
        overlap += max(0, min(gold_end, predicted_end) - max(gold_start, predicted_start))
        if gold_end <= predicted_end:
            gold_index += 1
        else:
            predicted_index += 1
    return overlap, predicted_size - overlap, gold_size - overlap


def _count_metric(
    counts: Sequence[tuple[int, int, int]],
    *,
    name: str,
    metric: str,
) -> dict[str, Any]:
    true_positive = sum(row[0] for row in counts)
    false_positive = sum(row[1] for row in counts)
    false_negative = sum(row[2] for row in counts)

    def ratio(selected: Sequence[float]) -> float:
        sampled = [counts[int(index)] for index in selected]
        tp = sum(row[0] for row in sampled)
        fp = sum(row[1] for row in sampled)
        fn = sum(row[2] for row in sampled)
        numerator, denominator = {
            "precision": (tp, tp + fp),
            "recall": (tp, tp + fn),
            "f1": (2 * tp, 2 * tp + fp + fn),
        }[metric]
        return numerator / denominator if denominator else 0.0

    numerator, effective_denominator = {
        "precision": (true_positive, true_positive + false_positive),
        "recall": (true_positive, true_positive + false_negative),
        "f1": (
            2 * true_positive,
            2 * true_positive + false_positive + false_negative,
        ),
    }[metric]
    if not counts or effective_denominator == 0:
        return _unavailable(denominator=len(counts), note="ZERO_METRIC_DENOMINATOR")
    indexes = list(map(float, range(len(counts))))
    lower, upper = bootstrap_interval(
        indexes,
        ratio,
        samples=BOOTSTRAP_SAMPLES,
        seed=_seed(name),
    )
    return {
        "status": "AVAILABLE",
        "value": _round(numerator / effective_denominator),
        "denominator": len(counts),
        "effectiveUnitDenominator": effective_denominator,
        "interval": {
            "method": "case-bootstrap",
            "confidenceLevel": 0.95,
            "samples": BOOTSTRAP_SAMPLES,
            "lower": _round(lower),
            "upper": _round(upper),
        },
    }


def _normalized_answer(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _compact_answer(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _claim_report(
    rows: Sequence[dict[str, Any]],
    *,
    dataset: str,
    exhaustive_claim_gold: bool,
    exhaustive_citation_gold: bool,
) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["kind"] == "claim_or_span_case":
            grouped[(row["slice"], row["task"])].append(row)
    slices = []
    has_precision_task = False
    for (slice_name, task), cases in sorted(grouped.items()):
        metrics: dict[str, Any]
        count_totals: dict[str, int] | None = None
        if task == "binary_classification":
            counts = [
                (
                    int(row["goldPositive"] and row["predictedPositive"]),
                    int(not row["goldPositive"] and row["predictedPositive"]),
                    int(row["goldPositive"] and not row["predictedPositive"]),
                )
                for row in cases
            ]
            agreements = sum(row["goldPositive"] == row["predictedPositive"] for row in cases)
            metrics = {"labelAgreementRate": _wilson_metric(agreements, len(cases))}
            has_precision_task = True
        elif task == "span_detection":
            span_pairs = [
                (
                    _validate_spans(row["goldSpans"], field="goldSpans"),
                    _validate_spans(row["predictedSpans"], field="predictedSpans"),
                )
                for row in cases
            ]
            counts = [_span_counts(gold, predicted) for gold, predicted in span_pairs]
            exact = sum(
                _merge_spans(gold) == _merge_spans(predicted) for gold, predicted in span_pairs
            )
            metrics = {"providedSpanExactMatchRate": _wilson_metric(exact, len(cases))}
            has_precision_task = True
        elif task == "exact_or_alias":
            matches = sum(
                _normalized_answer(row["prediction"])
                in {_normalized_answer(answer) for answer in row["goldAnswers"]}
                for row in cases
            )
            metrics = {"exactOrAliasRate": _wilson_metric(matches, len(cases))}
            counts = []
        else:
            coverage = []
            matches = 0
            for row in cases:
                prediction = _compact_answer(row["prediction"])
                group_hits = [
                    any(_compact_answer(alias) in prediction for alias in group)
                    for group in row["goldAnswerGroups"]
                ]
                coverage.append(sum(group_hits) / len(group_hits))
                matches += int(all(group_hits))
            metrics = {
                "answerGroupCoverage": _mean_metric(
                    coverage,
                    name=f"{dataset}:claim:{slice_name}:{task}:coverage",
                ),
                "allGroupsMatchedRate": _wilson_metric(matches, len(cases)),
            }
            counts = []
        if counts:
            count_totals = {
                "truePositive": sum(row[0] for row in counts),
                "falsePositive": sum(row[1] for row in counts),
                "falseNegative": sum(row[2] for row in counts),
            }
        for metric in ("precision", "recall", "f1"):
            if task in {"exact_or_alias", "answer_groups"}:
                metrics[metric] = _unavailable(
                    denominator=len(cases),
                    note="NOT_A_PRECISION_RECALL_TASK",
                )
            elif not exhaustive_claim_gold:
                metrics[metric] = _unavailable(
                    denominator=len(cases),
                    note="NON_EXHAUSTIVE_CLAIM_GOLD",
                )
            else:
                metrics[metric] = _count_metric(
                    counts,
                    name=f"{dataset}:claim:{slice_name}:{task}:{metric}",
                    metric=metric,
                )
        result: dict[str, Any] = {
            "slice": slice_name,
            "task": task,
            "caseDenominator": len(cases),
            "metrics": metrics,
        }
        if count_totals is not None:
            result["countTotals"] = count_totals
        slices.append(result)
    claim_availability = (
        "AVAILABLE_PER_SLICE" if exhaustive_claim_gold and has_precision_task else "UNAVAILABLE"
    )
    return {
        "status": "RUN" if slices else "NOT_RUN",
        "caseDenominator": sum(len(cases) for cases in grouped.values()),
        "metricAvailability": {
            "claimPrecision": claim_availability,
            "claimRecall": claim_availability,
            "claimF1": claim_availability,
            "citationPrecision": "UNAVAILABLE",
            "citationRecall": "UNAVAILABLE",
            "citationF1": "UNAVAILABLE",
        },
        "availabilityNoteCodes": {
            "claim": []
            if claim_availability != "UNAVAILABLE"
            else ["NON_EXHAUSTIVE_OR_NO_ELIGIBLE_CLAIM_GOLD"],
            "citation": [
                "NO_ASSERTION_CITATION_ROWS"
                if exhaustive_citation_gold
                else "NON_EXHAUSTIVE_CITATION_GOLD"
            ],
        },
        "sliceResults": slices,
    }


def _agent_report(
    rows: Sequence[dict[str, Any]],
    *,
    official_execution: bool,
) -> dict[str, Any]:
    agent_rows = [row for row in rows if row["kind"] == "agent_trial"]
    if not official_execution:
        return {
            "status": "NOT_RUN",
            "trialDenominator": 0,
            "caseDenominator": 0,
            "observedUnscoredTrialCount": len(agent_rows),
            "noteCodes": ["NO_OFFICIAL_EXTERNAL_AGENT_EXECUTION"],
            "metricAvailability": {
                "safeTrialRate": "UNAVAILABLE",
                "passPower": "UNAVAILABLE",
            },
            "sliceResults": [],
        }
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in agent_rows:
        grouped[row["slice"]][row["caseKey"]].append(row)
    slices = []
    for slice_name, cases in sorted(grouped.items()):
        trial_counts = {len(trials) for trials in cases.values()}
        if len(trial_counts) != 1:
            raise PublicTransferError(
                "official agent cases in a slice must have the same trial count"
            )
        k = next(iter(trial_counts))
        for trials in cases.values():
            if {row["trialNumber"] for row in trials} != set(range(1, k + 1)):
                raise PublicTransferError(
                    "official agent trial numbers must be contiguous from one"
                )
        trial_outcomes = [
            all(row["safe_success"].values()) for trials in cases.values() for row in trials
        ]
        case_outcomes = [
            all(all(row["safe_success"].values()) for row in trials) for trials in cases.values()
        ]
        slices.append(
            {
                "slice": slice_name,
                "trialsPerCase": k,
                "trialDenominator": len(trial_outcomes),
                "caseDenominator": len(case_outcomes),
                "metrics": {
                    "safeTrialRate": _wilson_metric(sum(trial_outcomes), len(trial_outcomes)),
                    "passPower": _wilson_metric(sum(case_outcomes), len(case_outcomes)),
                },
            }
        )
    return {
        "status": "RUN" if slices else "NOT_RUN",
        "trialDenominator": len(agent_rows),
        "caseDenominator": len({row["caseKey"] for row in agent_rows}),
        "noteCodes": [] if slices else ["NO_OFFICIAL_AGENT_TRIALS"],
        "metricAvailability": {
            "safeTrialRate": "AVAILABLE_PER_SLICE" if slices else "UNAVAILABLE",
            "passPower": "AVAILABLE_PER_SLICE" if slices else "UNAVAILABLE",
        },
        "sliceResults": slices,
    }


def build_report(manifest: Mapping[str, Any], rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Build a deterministic aggregate-only public-transfer report."""

    report = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        **GOVERNANCE,
        "datasetId": manifest["datasetId"],
        "source": {
            "officialUrl": manifest["officialUrl"],
            "license": manifest["license"],
            "upstreamRevisionOrCommit": manifest["upstreamRevisionOrCommit"],
            "perFileInventoryOrCanonicalInventorySha256": manifest[
                "perFileInventoryOrCanonicalInventorySha256"
            ],
            "selectionPolicy": manifest["selectionPolicy"],
            "normalizedInputSha256": manifest["normalizedInputSha256"],
        },
        "scorerVersion": SCORER_VERSION,
        "sourceManifestScorerVersion": manifest["scorerVersion"],
        "modelAndPromptFingerprintOrNOT_APPLICABLE": manifest[
            "modelAndPromptFingerprintOrNOT_APPLICABLE"
        ],
        "caseCountAndEligibleDenominators": dict(manifest["caseCountAndEligibleDenominators"]),
        "domains": {
            "ranking": _ranking_report(rows, dataset=manifest["datasetId"]),
            "claimOrSpan": _claim_report(
                rows,
                dataset=manifest["datasetId"],
                exhaustive_claim_gold=manifest["exhaustiveClaimGold"],
                exhaustive_citation_gold=manifest["exhaustiveCitationGold"],
            ),
            "agent": _agent_report(
                rows,
                official_execution=manifest["officialAgentExecution"],
            ),
        },
        "aggregationPolicy": "NO_WEIGHTED_TOTAL",
    }
    _assert_aggregate_only(report)
    return report


def _assert_aggregate_only(value: Any) -> None:
    if isinstance(value, Mapping):
        forbidden = _FORBIDDEN_OUTPUT_FIELDS.intersection(value)
        if forbidden:
            raise PublicTransferError("aggregate output contains a forbidden field")
        for child in value.values():
            _assert_aggregate_only(child)
    elif isinstance(value, list):
        for child in value:
            _assert_aggregate_only(child)


def _write_package(
    output: Path,
    *,
    report: dict[str, Any],
    source_manifest: Mapping[str, Any],
    source_manifest_sha256: str,
) -> Path:
    if output.exists() and not output.is_dir():
        raise PublicTransferError("output must be a directory")
    if output.is_dir() and any(path.name not in _OUTPUT_FILES for path in output.iterdir()):
        raise PublicTransferError("output directory contains unrelated files")
    report_path = output / "report.json"
    artifact_manifest_path = output / "manifest.json"
    atomic_write_json(report_path, report)
    artifact_manifest = {
        "schemaVersion": ARTIFACT_MANIFEST_SCHEMA_VERSION,
        **GOVERNANCE,
        "datasetId": source_manifest["datasetId"],
        "sourceManifestSha256": source_manifest_sha256,
        "normalizedInputSha256": source_manifest["normalizedInputSha256"],
        "reportSha256": sha256_file(report_path),
        "artifactFiles": ["manifest.json", "report.json", "SHA256SUMS"],
    }
    _assert_aggregate_only(artifact_manifest)
    atomic_write_json(artifact_manifest_path, artifact_manifest)
    sums = "".join(
        f"{sha256_file(output / name)}  {name}\n" for name in ("manifest.json", "report.json")
    )
    atomic_write_text(output / "SHA256SUMS", sums)
    return report_path


def run_import(*, manifest_path: Path, input_path: Path, output: Path) -> Path:
    """Validate, aggregate, and write one normalized public-transfer package."""
    artifact_paths = {(output / name).resolve() for name in _OUTPUT_FILES}
    if manifest_path.resolve() in artifact_paths or input_path.resolve() in artifact_paths:
        raise PublicTransferError("input files must be outside the output artifact paths")
    manifest = _validate_manifest(load_json(manifest_path), input_path=input_path)
    rows = load_jsonl(input_path)
    _validate_rows(rows, manifest)
    return _write_package(
        output,
        report=build_report(manifest, rows),
        source_manifest=manifest,
        source_manifest_sha256=sha256_file(manifest_path),
    )


def _self_check_rows() -> list[dict[str, Any]]:
    safe = {field: True for field in sorted(_SAFE_SUCCESS_FIELDS)}
    return [
        {
            "kind": "ranking_case",
            "caseKey": "ranking-1",
            "slice": "synthetic-ranking",
            "ranking": ["d2", "d1"],
            "qrels": {"d1": 2, "d2": 0},
        },
        {
            "kind": "claim_or_span_case",
            "caseKey": "claim-1",
            "slice": "synthetic-claim",
            "task": "binary_classification",
            "goldPositive": True,
            "predictedPositive": True,
        },
        {
            "kind": "claim_or_span_case",
            "caseKey": "span-1",
            "slice": "synthetic-span",
            "task": "span_detection",
            "goldSpans": [[0, 4]],
            "predictedSpans": [[0, 3]],
        },
        {
            "kind": "claim_or_span_case",
            "caseKey": "exact-1",
            "slice": "synthetic-exact",
            "task": "exact_or_alias",
            "prediction": "答案 A",
            "goldAnswers": ["答案 A", "A"],
        },
        {
            "kind": "agent_trial",
            "caseKey": "agent-1",
            "slice": "synthetic-agent",
            "trialNumber": 1,
            "safe_success": safe,
        },
    ]


def run_self_check(*, output: Path) -> Path:
    """Write deterministic synthetic evidence while keeping external Agent NOT_RUN."""

    rows = _self_check_rows()
    input_bytes = b"".join(canonical_json_bytes(row) + b"\n" for row in rows)
    manifest = {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "datasetId": "synthetic-self-check",
        "officialUrl": "https://example.invalid/aishop-public-transfer-self-check",
        "license": "NOT_APPLICABLE_SYNTHETIC",
        "upstreamRevisionOrCommit": "synthetic-v1",
        "perFileInventoryOrCanonicalInventorySha256": sha256_bytes(input_bytes),
        "selectionPolicy": "minimal deterministic synthetic schema and scorer check",
        "scorerVersion": SCORER_VERSION,
        "modelAndPromptFingerprintOrNOT_APPLICABLE": "NOT_APPLICABLE",
        "caseCountAndEligibleDenominators": {
            "caseCount": 5,
            "rankingCaseEligible": 1,
            "gradedRankingCaseEligible": 1,
            "binaryRankingCaseEligible": 1,
            "claimOrSpanCaseEligible": 3,
            "agentTrialEligible": 0,
            "agentCaseEligible": 0,
        },
        "normalizedInputSha256": sha256_bytes(input_bytes),
        "exhaustiveClaimGold": False,
        "exhaustiveCitationGold": False,
        "officialAgentExecution": False,
        **GOVERNANCE,
    }
    manifest = _validate_manifest(manifest, input_path=None)
    _validate_rows(rows, manifest)
    first = build_report(manifest, rows)
    second = build_report(manifest, rows)
    if canonical_json_bytes(first) != canonical_json_bytes(second):
        raise AssertionError("public-transfer self-check report is not deterministic")
    return _write_package(
        output,
        report=first,
        source_manifest=manifest,
        source_manifest_sha256=sha256_bytes(canonical_json_bytes(manifest)),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.self_check:
        if args.manifest is not None or args.input is not None:
            parser.error("--self-check cannot be combined with --manifest or --input")
        run_self_check(output=args.output)
    else:
        if args.manifest is None or args.input is None:
            parser.error("--manifest and --input are required unless --self-check is used")
        run_import(manifest_path=args.manifest, input_path=args.input, output=args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
