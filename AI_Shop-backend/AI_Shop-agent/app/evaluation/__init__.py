"""Shared evaluation contracts and artifact helpers."""

from app.evaluation.artifacts import EvaluationArtifactWriter, sha256_path
from app.evaluation.contracts import (
    EVAL_SCHEMA_VERSION,
    EvaluationAssertion,
    EvaluationCaseResult,
    EvaluationRun,
    EvaluationRunMetadata,
    aggregate_case_results,
)
from app.evaluation.ranking import (
    DEFAULT_K_VALUES,
    aggregate_ranking_cases,
    aggregate_stage_latency,
    bootstrap_mean_ci,
    paired_ranking_comparison,
    ranking_case_metrics,
)

__all__ = [
    "EVAL_SCHEMA_VERSION",
    "EvaluationArtifactWriter",
    "EvaluationAssertion",
    "EvaluationCaseResult",
    "EvaluationRun",
    "EvaluationRunMetadata",
    "aggregate_case_results",
    "DEFAULT_K_VALUES",
    "aggregate_ranking_cases",
    "aggregate_stage_latency",
    "bootstrap_mean_ci",
    "paired_ranking_comparison",
    "ranking_case_metrics",
    "sha256_path",
]
