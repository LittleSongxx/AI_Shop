from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evaluation.text2sql import CASE_SCHEMA_VERSION


class Outcome(StrEnum):
    ANSWER = "ANSWER"
    CLARIFY = "CLARIFY"
    ABSTAIN = "ABSTAIN"
    DENY = "DENY"


class Completion(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class Actor(ContractModel):
    admin_id: str = Field(alias="adminId", min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=64)
    permissions: list[str] = Field(default_factory=list)
    tenant_id: str | None = Field(default=None, alias="tenantId", max_length=64)


class PlanBranch(ContractModel):
    branch_id: str = Field(alias="branchId", min_length=1, max_length=64)
    semantic_view: str = Field(alias="semanticView", min_length=1, max_length=128)
    metrics: list[str] = Field(min_length=1, max_length=8)
    dimensions: list[str] = Field(default_factory=list, max_length=5)
    start_date: str | None = Field(default=None, alias="startDate")
    end_date: str | None = Field(default=None, alias="endDate")
    purpose: str = Field(default="", max_length=300)


class ResultOracle(ContractModel):
    mode: Literal["EXACT_ROWS", "EMPTY_ROWS", "NO_QUERY"]
    columns: list[str] = Field(default_factory=list)
    column_types: dict[str, dict[str, Any]] = Field(
        default_factory=dict, alias="columnTypes"
    )
    rows: list[dict[str, Any]] = Field(default_factory=list)
    order_sensitive: bool = Field(default=False, alias="orderSensitive")
    decimal_policy: Literal["EXACT_STRING"] = Field(
        default="EXACT_STRING", alias="decimalPolicy"
    )
    materialized: bool = False


class ClarificationOption(ContractModel):
    choice_id: str = Field(alias="choiceId", min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=100)
    answer_suffix: str = Field(alias="answerSuffix", min_length=1, max_length=200)


class ResourceBudget(ContractModel):
    max_rows: int = Field(default=200, alias="maxRows", ge=1, le=200)
    max_date_range_days: int = Field(default=90, alias="maxDateRangeDays", ge=1, le=90)
    max_result_bytes: int = Field(
        default=1_000_000, alias="maxResultBytes", ge=16_384, le=10_000_000
    )
    query_timeout_ms: int = Field(default=3_000, alias="queryTimeoutMs", ge=100, le=10_000)
    max_estimated_scan_rows: int | None = Field(
        default=None, alias="maxEstimatedScanRows", ge=1
    )


class Expected(ContractModel):
    outcome: Outcome
    completion: Completion
    reason_code: str | None = Field(default=None, alias="reasonCode", max_length=100)
    branches: list[PlanBranch] = Field(default_factory=list, max_length=3)
    reference_sql: list[str] = Field(default_factory=list, alias="referenceSql")
    result_oracle: ResultOracle = Field(alias="resultOracle")
    branch_result_oracles: list[ResultOracle] = Field(
        default_factory=list, alias="branchResultOracles", max_length=3
    )
    expected_failed_branch_ids: list[str] = Field(
        default_factory=list, alias="expectedFailedBranchIds", max_length=2
    )
    clarification_question: str | None = Field(
        default=None, alias="clarificationQuestion", max_length=300
    )
    clarification_options: list[ClarificationOption] = Field(
        default_factory=list, alias="clarificationOptions", max_length=8
    )
    required_facts: list[str] = Field(default_factory=list, alias="requiredFacts")
    forbidden_claims: list[str] = Field(default_factory=list, alias="forbiddenClaims")
    resource_budget: ResourceBudget = Field(
        default_factory=ResourceBudget, alias="resourceBudget"
    )
    max_model_calls: int = Field(default=2, alias="maxModelCalls", ge=0, le=8)
    max_query_count: int = Field(default=1, alias="maxQueryCount", ge=0, le=8)

    @model_validator(mode="after")
    def validate_outcome_contract(self) -> "Expected":
        if self.outcome is Outcome.ANSWER:
            if not self.branches or not self.reference_sql:
                raise ValueError("ANSWER requires branches and referenceSql")
            if len(self.reference_sql) != len(self.branches):
                raise ValueError("ANSWER requires one referenceSql per branch")
            if self.branch_result_oracles and len(self.branch_result_oracles) != len(
                self.branches
            ):
                raise ValueError("branchResultOracles must align with branches")
            if self.result_oracle.mode == "NO_QUERY":
                raise ValueError("ANSWER requires a query result oracle")
            if self.completion not in {Completion.COMPLETE, Completion.PARTIAL}:
                raise ValueError("ANSWER completion must be COMPLETE or PARTIAL")
            branch_ids = {branch.branch_id for branch in self.branches}
            if not set(self.expected_failed_branch_ids).issubset(branch_ids):
                raise ValueError("expectedFailedBranchIds must name plan branches")
            if self.completion is Completion.PARTIAL and not self.expected_failed_branch_ids:
                raise ValueError("PARTIAL requires expectedFailedBranchIds")
            if self.completion is Completion.COMPLETE and self.expected_failed_branch_ids:
                raise ValueError("COMPLETE must not contain expectedFailedBranchIds")
        else:
            if (
                self.branches
                or self.reference_sql
                or self.branch_result_oracles
                or self.expected_failed_branch_ids
            ):
                raise ValueError("non-ANSWER outcome must not contain SQL or plan branches")
            if self.result_oracle.mode != "NO_QUERY":
                raise ValueError("non-ANSWER outcome requires NO_QUERY oracle")
            if self.completion is not Completion.NOT_APPLICABLE:
                raise ValueError("non-ANSWER completion must be NOT_APPLICABLE")
        if self.outcome is Outcome.CLARIFY:
            if not self.clarification_question or len(self.clarification_options) < 2:
                raise ValueError("CLARIFY requires a question and at least two options")
        elif self.clarification_question or self.clarification_options:
            raise ValueError("clarification fields are only valid for CLARIFY")
        if self.outcome in {Outcome.ABSTAIN, Outcome.DENY} and not self.reason_code:
            raise ValueError("ABSTAIN and DENY require reasonCode")
        return self


class FlowContract(ContractModel):
    page_size: int | None = Field(default=None, alias="pageSize", ge=1, le=200)
    traverse_all_pages: bool = Field(default=False, alias="traverseAllPages")
    export_frozen_result: bool = Field(default=False, alias="exportFrozenResult")
    follow_clarification: bool = Field(default=False, alias="followClarification")
    expected_second_outcome: Outcome | None = Field(default=None, alias="expectedSecondOutcome")
    fault: str | None = Field(default=None, max_length=100)


class Annotation(ContractModel):
    status: Literal[
        "AI_DRAFT_PENDING_HUMAN_REVIEW",
        "HUMAN_VERIFIED",
        "HUMAN_REVIEWED_ADJUDICATED",
    ] = "AI_DRAFT_PENDING_HUMAN_REVIEW"
    human_decision_authority: bool = Field(default=False, alias="humanDecisionAuthority")
    ai_assistance_used: bool = Field(default=True, alias="aiAssistanceUsed")
    reviewers: list[str] = Field(default_factory=list, max_length=2)
    adjudicator: str | None = Field(default=None, max_length=100)
    review_evidence: dict[str, str] = Field(default_factory=dict, alias="reviewEvidence")

    @model_validator(mode="after")
    def validate_human_status(self) -> "Annotation":
        if self.status == "AI_DRAFT_PENDING_HUMAN_REVIEW":
            if self.human_decision_authority or self.reviewers or self.review_evidence:
                raise ValueError("draft annotation must not claim human review")
            return self
        if not self.human_decision_authority:
            raise ValueError("human-reviewed annotation requires humanDecisionAuthority=true")
        if len(self.reviewers) != 2 or len(set(self.reviewers)) != 2:
            raise ValueError("human-reviewed annotation requires two distinct reviewers")
        required_hashes = {"sourceDatasetSha256", "reviewASha256", "reviewBSha256"}
        if not required_hashes.issubset(self.review_evidence):
            raise ValueError("human-reviewed annotation is missing review evidence hashes")
        if any(
            len(self.review_evidence[name]) != 64
            for name in required_hashes
        ):
            raise ValueError("review evidence values must be SHA-256 digests")
        if self.status == "HUMAN_REVIEWED_ADJUDICATED" and not self.adjudicator:
            raise ValueError("adjudicated annotation requires an adjudicator")
        return self


class Text2SqlCase(ContractModel):
    schema_version: Literal[CASE_SCHEMA_VERSION] = Field(
        default=CASE_SCHEMA_VERSION, alias="schemaVersion"
    )
    case_id: str = Field(alias="id", pattern=r"^t2s-v0-[0-9]{3}$")
    split: Literal["development"] = "development"
    lifecycle: Literal[
        "AI_DRAFT_PENDING_HUMAN_REVIEW",
        "HUMAN_VERIFIED",
        "HUMAN_REVIEWED_ADJUDICATED",
    ] = "AI_DRAFT_PENDING_HUMAN_REVIEW"
    question: str = Field(min_length=2, max_length=500)
    actor: Actor
    fixture_state: Literal["base", "boundary", "empty"] = Field(alias="fixtureState")
    expected: Expected
    flow: FlowContract = Field(default_factory=FlowContract)
    slice_tags: list[str] = Field(alias="sliceTags", min_length=1)
    risk: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    annotation: Annotation = Field(default_factory=Annotation)
    annotation_note: str = Field(default="", alias="annotationNote", max_length=1000)

    def public(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")
