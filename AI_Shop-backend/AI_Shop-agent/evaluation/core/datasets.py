from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from evaluation.core.config import load_suite
from evaluation.core.contracts import (
    CASE_SCHEMA_VERSION_V3,
    DATASET_LOCK_SCHEMA_VERSION,
    DATASET_LOCK_SCHEMA_VERSION_V3,
    SUPPORTED_CASE_SCHEMA_VERSIONS,
    SUPPORTED_DATASET_LOCK_SCHEMA_VERSIONS,
    Domain,
    EvaluationCase,
    Split,
    ValidationError,
)
from evaluation.core.io import (
    EVALUATION_ROOT,
    atomic_write_json,
    canonical_json_bytes,
    load_json,
    load_jsonl,
    relative_to_repo,
    sha256_bytes,
    sha256_file,
    utc_now,
)

DATASETS_ROOT = EVALUATION_ROOT / "datasets"
LOCKS_ROOT = DATASETS_ROOT / "locks"
_CASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{5,95}$")
_PROVIDERS = {"embedding", "rerank", "llm", "agent-runtime"}


def dataset_files(split: Split) -> list[Path]:
    return sorted((DATASETS_ROOT / split.value).glob("*.jsonl"))


def lock_path(split: Split) -> Path:
    return LOCKS_ROOT / f"{split.value}.lock.json"


def _strings(value: Any, *, field: str, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be an array")
    result = tuple(str(item).strip() for item in value if str(item).strip())
    if not allow_empty and not result:
        raise ValidationError(f"{field} must not be empty")
    if len(result) != len(set(result)):
        raise ValidationError(f"{field} contains duplicates")
    return result


def _validate_search(case_id: str, value: dict[str, Any]) -> None:
    from evaluation.core.catalog import load_catalog_fixture

    expected = value["expected"]
    query = str(value["input"].get("query") or "").strip()
    if not query:
        raise ValidationError(f"{case_id}: search input.query is required")
    qrels = expected.get("qrels")
    if not isinstance(qrels, dict):
        raise ValidationError(f"{case_id}: search expected.qrels must be an object")
    try:
        normalized_qrels = {str(key): int(grade) for key, grade in qrels.items()}
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{case_id}: qrel grades must be integers") from exc
    if any(grade < 0 or grade > 3 for grade in normalized_qrels.values()):
        raise ValidationError(f"{case_id}: qrel grades must be between zero and three")
    no_result = bool(expected.get("noResult"))
    if no_result and any(grade > 0 for grade in normalized_qrels.values()):
        raise ValidationError(f"{case_id}: no-result case cannot have relevant qrels")
    if not no_result and not any(grade > 0 for grade in normalized_qrels.values()):
        raise ValidationError(f"{case_id}: answerable search case needs a relevant qrel")
    mode = str(expected.get("judgmentMode") or "")
    if mode not in {"EXHAUSTIVE_CATALOG", "JUDGED_POOL"}:
        raise ValidationError(f"{case_id}: judgmentMode must be EXHAUSTIVE_CATALOG or JUDGED_POOL")
    if mode == "JUDGED_POOL":
        pool = _strings(
            expected.get("judgedDocumentIds"),
            field=f"{case_id}.expected.judgedDocumentIds",
        )
        if not set(normalized_qrels).issubset(pool):
            raise ValidationError(f"{case_id}: qrels must be inside the judged pool")
    fixture = load_catalog_fixture(expected_sha256=str(expected.get("catalogSha256") or ""))
    current_fixture = load_catalog_fixture()
    if value.get("split") != Split.FINAL.value and (
        expected.get("catalogSha256") != current_fixture.get("canonicalSha256")
    ):
        raise ValidationError(f"{case_id}: search case is not bound to the current catalog fixture")
    if fixture.get("schemaVersion") == "aishop-evaluation-product-catalog/v2":
        catalog = {str(item.get("productId") or ""): item for item in fixture["products"]}
        unavailable = sorted(
            product_id
            for product_id, grade in normalized_qrels.items()
            if grade > 0
            and (
                product_id not in catalog
                or catalog[product_id].get("authoritativeAvailable") is not True
            )
        )
        if unavailable:
            raise ValidationError(
                f"{case_id}: positive qrels are not authoritatively available: "
                + ", ".join(unavailable)
            )


def _validate_rag(case_id: str, value: dict[str, Any]) -> None:
    expected = value["expected"]
    if not str(value["input"].get("query") or "").strip():
        raise ValidationError(f"{case_id}: rag input.query is required")
    fact_ids = _strings(
        expected.get("relevantFactIds") or [],
        field=f"{case_id}.expected.relevantFactIds",
        allow_empty=True,
    )
    no_answer = bool(expected.get("noAnswer"))
    if no_answer and fact_ids:
        raise ValidationError(f"{case_id}: no-answer case cannot declare relevant facts")
    if not no_answer and not fact_ids:
        raise ValidationError(f"{case_id}: answerable RAG case needs relevant fact IDs")
    claims = expected.get("requiredClaims") or []
    if not no_answer and not isinstance(claims, list):
        raise ValidationError(f"{case_id}: requiredClaims must be an array")
    if not no_answer and not claims:
        raise ValidationError(f"{case_id}: answerable RAG case needs required claims")
    for index, claim in enumerate(claims, 1):
        if not isinstance(claim, dict):
            raise ValidationError(f"{case_id}: required claim {index} must be an object")
        pattern_groups = claim.get("patternGroups")
        if pattern_groups is None:
            _strings(
                claim.get("patterns") or [],
                field=f"{case_id}.requiredClaims[{index}].patterns",
            )
        else:
            if not isinstance(pattern_groups, list) or not pattern_groups:
                raise ValidationError(
                    f"{case_id}.requiredClaims[{index}].patternGroups must be a non-empty array"
                )
            for group_index, group in enumerate(pattern_groups, 1):
                _strings(
                    group,
                    field=(
                        f"{case_id}.requiredClaims[{index}]."
                        f"patternGroups[{group_index}]"
                    ),
                )
        claim_facts = _strings(
            claim.get("factIds") or [],
            field=f"{case_id}.requiredClaims[{index}].factIds",
        )
        if not set(claim_facts).issubset(fact_ids):
            raise ValidationError(f"{case_id}: required claim facts must be relevant facts")
    attack = expected.get("attack")
    if attack is not None:
        if not isinstance(attack, dict) or attack.get("type") not in {"pure", "mixed"}:
            raise ValidationError(f"{case_id}: attack.type must be pure or mixed")


def _validate_agent(case_id: str, value: dict[str, Any]) -> None:
    expected = value["expected"]
    turns = value["input"].get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValidationError(f"{case_id}: agent input.turns must not be empty")
    for index, turn in enumerate(turns, 1):
        if not isinstance(turn, dict) or not str(turn.get("message") or "").strip():
            raise ValidationError(f"{case_id}: agent turn {index} needs a message")
    terminal = _strings(
        expected.get("terminalStatuses") or [],
        field=f"{case_id}.expected.terminalStatuses",
    )
    if not set(terminal).issubset(
        {
            "SUCCEEDED",
            "FAILED",
            "CANCELLED",
            "HANDOFF",
            "DEGRADED",
            "FALLBACK",
            "INCONCLUSIVE",
            "MANUAL_REVIEW",
        }
    ):
        raise ValidationError(f"{case_id}: unsupported terminal status")
    _strings(
        expected.get("requiredTools") or [],
        field=f"{case_id}.expected.requiredTools",
        allow_empty=True,
    )
    if "outputPatterns" in expected:
        patterns = expected.get("outputPatterns")
        if (
            not isinstance(patterns, list)
            or any(not isinstance(item, str) or not item.strip() for item in patterns)
        ):
            raise ValidationError(
                f"{case_id}.expected.outputPatterns must be an array of non-empty strings"
            )


def _optional_object(value: Any, *, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValidationError(f"{field} must be an object or null")
    return dict(value)


def _optional_object_array(value: Any, *, field: str) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValidationError(f"{field} must be an array of objects")
    return tuple(dict(item) for item in value)


def _validate_v3_extensions(case_id: str, value: dict[str, Any], domain: Domain) -> None:
    repeat = value.get("repeatPolicy")
    if repeat is not None:
        if domain is not Domain.AGENT:
            raise ValidationError(f"{case_id}: repeatPolicy is only valid for Agent cases")
        if not isinstance(repeat, dict):
            raise ValidationError(f"{case_id}.repeatPolicy must be an object")
        if repeat.get("k") is not None:
            try:
                k = int(repeat["k"])
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"{case_id}.repeatPolicy.k must be an integer") from exc
            if k < 1 or k > 32:
                raise ValidationError(f"{case_id}.repeatPolicy.k must be between 1 and 32")
    recovery = value.get("faultRecoveryContract")
    if recovery is not None:
        if not isinstance(recovery, dict) or not recovery:
            raise ValidationError(f"{case_id}.faultRecoveryContract must be a non-empty object")
        terminal = recovery.get("terminalState")
        if terminal is not None and str(terminal) not in {
            "SUCCEEDED",
            "FAILED",
            "DEGRADED",
            "FALLBACK",
            "INCONCLUSIVE",
            "MANUAL_REVIEW",
        }:
            raise ValidationError(
                f"{case_id}.faultRecoveryContract.terminalState is unsupported"
            )
    state_fixture = _optional_object(value.get("stateFixture"), field=f"{case_id}.stateFixture")
    if state_fixture and domain is Domain.AGENT:
        provision = state_fixture.get("provision")
        if provision is not None:
            if not isinstance(provision, dict):
                raise ValidationError(f"{case_id}.stateFixture.provision must be an object")
            if provision.get("kind") != "CANCELABLE_ORDER_V1":
                raise ValidationError(
                    f"{case_id}.stateFixture.provision.kind is unsupported"
                )
            if provision.get("scope") != "LOCAL_EVALUATION_ONLY":
                raise ValidationError(
                    f"{case_id}.stateFixture.provision.scope must be LOCAL_EVALUATION_ONLY"
                )
            state_mode = str(
                (value.get("expected") or {}).get("stateMode")
                or (value.get("repeatPolicy") or {}).get("stateMode")
                or "READ_ONLY"
            )
            if state_mode not in {"READ_ONLY", "PROPOSE_ONLY", "WRITE_CONFIRMED"}:
                raise ValidationError(
                    f"{case_id}: provisioned Agent fixture has unsupported stateMode"
                )
            if state_mode == "WRITE_CONFIRMED" and not (
                (value.get("expected") or {}).get("confirmationFlow")
            ):
                raise ValidationError(
                    f"{case_id}: WRITE_CONFIRMED fixture requires confirmationFlow"
                )
    assertions = _optional_object_array(
        value.get("stateAssertions"), field=f"{case_id}.stateAssertions"
    )
    for index, assertion_value in enumerate(assertions, 1):
        if not str(assertion_value.get("path") or "").strip():
            raise ValidationError(
                f"{case_id}.stateAssertions[{index}].path must not be empty"
            )


def parse_case(value: dict[str, Any], *, expected_split: Split | None = None) -> EvaluationCase:
    schema_version = str(value.get("schemaVersion") or "")
    if schema_version not in SUPPORTED_CASE_SCHEMA_VERSIONS:
        raise ValidationError(
            "case schema must be one of "
            f"{sorted(SUPPORTED_CASE_SCHEMA_VERSIONS)}, got {schema_version!r}"
        )
    case_id = str(value.get("id") or "").strip()
    if not _CASE_ID_RE.fullmatch(case_id):
        raise ValidationError(f"invalid case id: {case_id!r}")
    try:
        split = Split(str(value.get("split") or ""))
        domain = Domain(str(value.get("domain") or ""))
    except ValueError as exc:
        raise ValidationError(f"{case_id}: invalid split or domain") from exc
    if expected_split is not None and split is not expected_split:
        raise ValidationError(
            f"{case_id}: split {split.value} does not match directory {expected_split.value}"
        )
    if not isinstance(value.get("input"), dict) or not isinstance(value.get("expected"), dict):
        raise ValidationError(f"{case_id}: input and expected must be objects")
    required_providers = _strings(
        value.get("requiredProviders") or [],
        field=f"{case_id}.requiredProviders",
    )
    unknown = set(required_providers).difference(_PROVIDERS)
    if unknown:
        raise ValidationError(f"{case_id}: unknown required providers {sorted(unknown)}")
    tags = _strings(
        value.get("tags") or [],
        field=f"{case_id}.tags",
        allow_empty=True,
    )
    slice_tags = _strings(
        value.get("sliceTags") or [],
        field=f"{case_id}.sliceTags",
        allow_empty=True,
    )
    _validate_v3_extensions(case_id, value, domain)
    if domain is Domain.SEARCH:
        _validate_search(case_id, value)
    elif domain is Domain.RAG:
        _validate_rag(case_id, value)
    else:
        _validate_agent(case_id, value)
    return EvaluationCase(
        case_id=case_id,
        split=split,
        domain=domain,
        input=dict(value["input"]),
        expected=dict(value["expected"]),
        required_providers=required_providers,
        tags=tags,
        slice_tags=slice_tags,
        state_fixture=_optional_object(
            value.get("stateFixture"), field=f"{case_id}.stateFixture"
        ),
        state_assertions=_optional_object_array(
            value.get("stateAssertions"), field=f"{case_id}.stateAssertions"
        ),
        repeat_policy=_optional_object(
            value.get("repeatPolicy"), field=f"{case_id}.repeatPolicy"
        ),
        fault_recovery_contract=_optional_object(
            value.get("faultRecoveryContract"),
            field=f"{case_id}.faultRecoveryContract",
        ),
        schema_version=schema_version,
    )


def load_split(split: Split, *, files: Iterable[Path] | None = None) -> list[EvaluationCase]:
    paths = list(files) if files is not None else dataset_files(split)
    if not paths:
        raise ValidationError(f"no {split.value} dataset files found")
    cases = [parse_case(row, expected_split=split) for path in paths for row in load_jsonl(path)]
    validate_unique_cases(cases)
    return cases


def case_content_sha256(case: EvaluationCase) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "domain": case.domain.value,
                "input": case.input,
            }
        )
    )


def validate_unique_cases(cases: Iterable[EvaluationCase]) -> None:
    rows = list(cases)
    ids = Counter(case.case_id for case in rows)
    duplicates = sorted(case_id for case_id, count in ids.items() if count > 1)
    if duplicates:
        raise ValidationError(f"duplicate case IDs: {duplicates}")
    content = Counter(case_content_sha256(case) for case in rows)
    duplicated_content = sorted(digest for digest, count in content.items() if count > 1)
    if duplicated_content:
        raise ValidationError(
            "duplicate case inputs are not allowed, even under different IDs: "
            + ", ".join(duplicated_content)
        )


def canonical_dataset_sha256(cases: Iterable[EvaluationCase]) -> str:
    rows = sorted((case.public() for case in cases), key=lambda item: item["id"])
    return sha256_bytes(canonical_json_bytes(rows))


def build_lock(split: Split, *, write: bool = True) -> dict[str, Any]:
    files = dataset_files(split)
    cases = load_split(split, files=files)
    counts = Counter(case.domain.value for case in cases)
    lock_created_at = utc_now()
    lock_schema = (
        DATASET_LOCK_SCHEMA_VERSION_V3
        if any(case.schema_version == CASE_SCHEMA_VERSION_V3 for case in cases)
        else DATASET_LOCK_SCHEMA_VERSION
    )
    lock = {
        "schemaVersion": lock_schema,
        "split": split.value,
        "createdAt": lock_created_at,
        "canonicalDatasetSha256": canonical_dataset_sha256(cases),
        "caseCount": len(cases),
        "domainCounts": dict(sorted(counts.items())),
        "files": {
            relative_to_repo(path): {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in files
        },
    }
    if write and lock_path(split).is_file():
        try:
            previous = load_json(lock_path(split))
        except (OSError, ValueError):
            previous = None
        if isinstance(previous, dict) and previous.get("createdAt"):
            previous_without_time = dict(previous)
            previous_without_time.pop("createdAt", None)
            current_without_time = dict(lock)
            current_without_time.pop("createdAt", None)
            if previous_without_time == current_without_time:
                lock["createdAt"] = previous["createdAt"]
    if write:
        atomic_write_json(lock_path(split), lock)
    return lock


def verify_lock(split: Split) -> dict[str, Any]:
    path = lock_path(split)
    if not path.is_file():
        raise ValidationError(f"missing dataset lock: {path}")
    expected = load_json(path)
    if expected.get("schemaVersion") not in SUPPORTED_DATASET_LOCK_SCHEMA_VERSIONS:
        raise ValidationError(f"unsupported dataset lock schema: {expected.get('schemaVersion')!r}")
    actual = build_lock(split, write=False)
    for volatile in ("createdAt",):
        expected.pop(volatile, None)
        actual.pop(volatile, None)
    if expected != actual:
        raise ValidationError(f"{split.value} dataset lock does not match current files")
    return load_json(path)


def validate_repository_datasets() -> dict[str, Any]:
    from evaluation.core.catalog import load_catalog_fixture

    load_catalog_fixture()
    suite = load_suite()
    all_cases: list[EvaluationCase] = []
    result: dict[str, Any] = {}
    for split in (Split.DEVELOPMENT, Split.REGRESSION):
        lock = verify_lock(split)
        cases = load_split(split)
        counts = Counter(case.domain.value for case in cases)
        minimums = suite["splitMinimums"][split.value]
        missing = {
            domain: int(minimums[domain]) - counts.get(domain, 0)
            for domain in minimums
            if counts.get(domain, 0) < int(minimums[domain])
        }
        if missing:
            raise ValidationError(f"{split.value} dataset is below predeclared minimums: {missing}")
        # Search slices are part of the visible regression contract. A run
        # must not appear healthy merely because a required slice disappeared
        # from the denominator.
        required_search_slices = (
            suite.get("slicePolicy", {}).get("search", {}).get("required") or []
        )
        if required_search_slices:
            present = {
                tag
                for case in cases
                if case.domain is Domain.SEARCH
                for tag in case.slice_tags
            }
            missing_slices = sorted(set(map(str, required_search_slices)) - present)
            if missing_slices:
                raise ValidationError(
                    f"{split.value} search dataset is missing required slices: {missing_slices}"
                )
        all_cases.extend(cases)
        result[split.value] = lock
    validate_unique_cases(all_cases)
    return result


def validate_final_against_known(final_cases: list[EvaluationCase]) -> None:
    known = [
        *load_split(Split.DEVELOPMENT),
        *load_split(Split.REGRESSION),
    ]
    validate_unique_cases([*known, *final_cases])
    suite = load_suite()
    minimums = suite["splitMinimums"][Split.FINAL.value]
    counts = Counter(case.domain.value for case in final_cases)
    missing = {
        domain: int(minimums[domain]) - counts.get(domain, 0)
        for domain in minimums
        if counts.get(domain, 0) < int(minimums[domain])
    }
    if missing:
        raise ValidationError(f"final dataset is below predeclared minimums: {missing}")
    if any(case.schema_version != CASE_SCHEMA_VERSION_V3 for case in final_cases):
        raise ValidationError("final dataset must use the v3 case schema")

    # Final slice counts are exact, and every case has exactly one mutually
    # exclusive quality slice. This is checked again at claim time; generator
    # self-checks are not sufficient evidence.
    slice_policy = suite.get("slicePolicy") or {}
    expected_by_domain: dict[str, dict[str, int]] = {}
    for domain in ("search", "rag", "agent"):
        configured = (slice_policy.get(domain) or {}).get("finalCounts") or {}
        if configured:
            expected_by_domain[domain] = {
                str(key): int(value) for key, value in configured.items()
            }
    for domain, expected_slices in expected_by_domain.items():
        domain_cases = [case for case in final_cases if case.domain.value == domain]
        if any(len(case.slice_tags) != 1 for case in domain_cases):
            raise ValidationError(f"final {domain} cases must contain exactly one slice tag")
        actual = Counter(case.slice_tags[0] for case in domain_cases)
        expected = Counter(expected_slices)
        if actual != expected:
            raise ValidationError(
                f"final {domain} slice counts differ: expected {dict(expected)}, got {dict(actual)}"
            )
