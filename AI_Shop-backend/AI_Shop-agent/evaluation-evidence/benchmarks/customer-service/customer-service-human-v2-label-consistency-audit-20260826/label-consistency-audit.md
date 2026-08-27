# Customer-service v2 label-consistency audit

- Status: `BLOCKED_HUMAN_READJUDICATION`
- Dataset: `120` cases / `ab5129a73cf6f986173d92e3f5f04ab7e8689bae9ad4c7d7294fa13b587ee079`
- Findings: `5`; blocking: `3`; affected cases: `25`
- Release gate eligible: `false`

The audit does not change a human label. It shows where the same schema was applied with conflicting semantics, so current point estimates remain development diagnostics.

## Findings

### BLOCKING — `TAXONOMY_RECOMMENT_ACTION_COLLISION`

RECOMMENT gold labels route catalog refinement into an order-review write action

Cases: `cs-candidate-v2-112, cs-candidate-v2-113, cs-candidate-v2-114`

Required resolution: Blindly re-annotate the affected cases under taxonomy v2.1, then adjudicate; do not mutate the existing immutable dataset.

### BLOCKING — `SLOT_AMOUNT_SPAN_POLICY_SPLIT`

amount alternates between a numeric core and the original visible currency span

Cases: `cs-candidate-v2-085, cs-candidate-v2-086, cs-candidate-v2-088, cs-candidate-v2-089, cs-candidate-v2-097, cs-candidate-v2-111, cs-candidate-v2-119, cs-gold-v1-001, cs-gold-v1-002, cs-gold-v1-009, cs-gold-v1-020, cs-gold-v1-029, cs-gold-v1-030, cs-gold-v1-034, cs-gold-v1-042, cs-gold-v1-058`

Required resolution: Choose one raw-span convention, document normalization separately, and re-adjudicate every amount-bearing case under that convention.

### BLOCKING — `SLOT_BUDGET_COMPLETENESS_SPLIT`

equivalent budget language is not annotated with a consistent slot set

Cases: `cs-candidate-v2-097, cs-candidate-v2-099, cs-gold-v1-001, cs-gold-v1-002, cs-gold-v1-029, cs-gold-v1-034, cs-gold-v1-042`

Required resolution: Define whether budget is canonical or derived and re-adjudicate all budget cases.

### MAJOR — `SLOT_QUANTITY_SCOPE_SPLIT`

explicit occurrence counts are sometimes quantity slots and sometimes omitted

Cases: `cs-candidate-v2-069, cs-candidate-v2-073, cs-candidate-v2-088, cs-gold-v1-037, cs-gold-v1-046, cs-gold-v1-052`

Required resolution: Separate item quantity from occurrence/frequency, or explicitly exclude frequency from quantity, then re-adjudicate both groups.

### MAJOR — `SLOT_PRODUCT_FEATURE_COMPOSITION_SPLIT`

feature text is inconsistently duplicated inside productName

Cases: `cs-candidate-v2-097, cs-gold-v1-034`

Required resolution: Define non-overlapping canonical entity spans and re-adjudicate compound product names.

## Metric validity

- `intentMacroF1`: `CONFOUNDED_BY_TAXONOMY_COLLISION`
- `slotEntitySpanF1`: `CONFOUNDED_BY_LABEL_POLICY_SPLITS`
- `slotExactMatch`: `NOT_VALID_AS_STRICT_SCHEMA_METRIC_UNTIL_READJUDICATED`
- `highRiskIntentRecall`: `DEVELOPMENT_DIAGNOSTIC_ONLY_PROVENANCE_PENDING`
- `handoffRecall`: `DEVELOPMENT_DIAGNOSTIC_ONLY_PROVENANCE_PENDING`
- `criticalHandoffMissRate`: `DEVELOPMENT_DIAGNOSTIC_ONLY_SMALL_DENOMINATOR`
- `answerCorrectness`: `NOT_MEASURED_BY_INPUT_GOLD`

## Required workflow

- Freeze this audit; do not edit the 120-case HUMAN_VERIFIED artifact in place.
- Give the blind re-annotation sheet and taxonomy v2.1 to independent reviewers.
- Seal two completed sheets, measure agreement, adjudicate every disagreement, and publish a successor dataset hash.
- Re-run rule and HTTP evaluation from the successor dataset; do not carry forward current point estimates.
