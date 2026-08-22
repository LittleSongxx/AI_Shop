# AI Shop AI evaluation

- Run: final-20260822-ai-quality-v7
- Split: final
- Dataset SHA-256: 672755f3a626ca554ee09b4bf9e7e275664b30070ed873c902968e1bcd870b93
- Execution mode: LOCAL_FULL_STACK
- Overall gate: FAIL

## Domain gates

- search: PASS
- rag: FAIL
- agent: PASS

## Metrics

### search
- executionRate: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- recallAt3: 0.92803 (n=44, 95% CI [0.859848, 0.988636] (percentile-bootstrap))
- recallAt5: 0.962121 (n=44, 95% CI [0.916667, 1.0] (percentile-bootstrap))
- recallAt10: 0.962121 (n=44, 95% CI [0.912879, 1.0] (percentile-bootstrap))
- mrrAt10: 0.9375 (n=44, 95% CI [0.880682, 0.988636] (percentile-bootstrap))
- ndcgAt5: 0.920521 (n=44, 95% CI [0.869389, 0.963878] (percentile-bootstrap))
- ndcgAt10: 0.920521 (n=44, 95% CI [0.867881, 0.965531] (percentile-bootstrap))
- noResultAccuracy: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- constraintViolationCount: 0.0 (n=50)
- hardConstraintSatisfaction: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- providerCompleteness: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- runtimeErrorCount: 0.0 (n=50)
- latencyMsP50: 310.338279 (n=50, 95% CI [216.264357, 383.076719] (percentile-bootstrap))
- latencyMsP95: 1063.570987 (n=50, 95% CI [597.070637, 1638.102004] (percentile-bootstrap))
- latencyMsP99: 1628.315822 (n=50, 95% CI [760.263411, 1748.196545] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

### rag
- executionRate: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- retrievalRecallAt3: 1.0 (n=29, 95% CI [1.0, 1.0] (percentile-bootstrap))
- retrievalRecallAt5: 1.0 (n=29, 95% CI [1.0, 1.0] (percentile-bootstrap))
- retrievalMrrAt10: 1.0 (n=29, 95% CI [1.0, 1.0] (percentile-bootstrap))
- retrievalNdcgAt5: 1.0 (n=29, 95% CI [1.0, 1.0] (percentile-bootstrap))
- sourcePrecision: 1.0 (n=29, 95% CI [1.0, 1.0] (percentile-bootstrap))
- sourceCoverage: 1.0 (n=29, 95% CI [1.0, 1.0] (percentile-bootstrap))
- generationCorrectness: 0.98 (n=50, 95% CI [0.895046, 0.996461] (wilson))
- requiredClaimCompleteness: 0.98 (n=50, 95% CI [0.94, 1.0] (percentile-bootstrap))
- citationSupport: 0.98 (n=50, 95% CI [0.94, 1.0] (percentile-bootstrap))
- groundedFaithfulness: 0.993333 (n=50, 95% CI [0.98, 1.0] (percentile-bootstrap))
- noAnswerAccuracy: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- injectionResistance: 1.0 (n=8, 95% CI [0.675592, 1.0] (wilson))
- invalidCitationCount: 0.0 (n=50)
- severeSafetyViolationCount: 0.0 (n=50)
- providerCompleteness: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- queryExpansionFailureCount: 3.0 (n=50)
- runtimeErrorCount: 0.0 (n=50)
- latencyMsP50: 1989.934148 (n=50, 95% CI [1707.7703, 2333.077576] (percentile-bootstrap))
- latencyMsP95: 4339.154494 (n=50, 95% CI [3352.065977, 4826.20693] (percentile-bootstrap))
- latencyMsP99: 4825.515848 (n=50, 95% CI [4059.652104, 4833.981597] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

### agent
- executionRate: 1.0 (n=25, 95% CI [0.866808, 1.0] (wilson))
- taskSuccess: 1.0 (n=25, 95% CI [0.866808, 1.0] (wilson))
- executionCompleteness: 1.0 (n=25, 95% CI [0.866808, 1.0] (wilson))
- toolSelectionAccuracy: 1.0 (n=25, 95% CI [0.866808, 1.0] (wilson))
- toolArgumentAccuracy: 1.0 (n=25, 95% CI [0.866808, 1.0] (wilson))
- terminalStateCorrectness: 1.0 (n=25, 95% CI [0.866808, 1.0] (wilson))
- retryIdempotency: 1.0 (n=22, 95% CI [0.851345, 1.0] (wilson))
- stateDiffMatch: 1.0 (n=25, 95% CI [0.866808, 1.0] (wilson))
- duplicateSideEffectCount: 0.0 (n=25)
- providerCompleteness: 1.0 (n=25, 95% CI [0.866808, 1.0] (wilson))
- severeSafetyViolationCount: 0.0 (n=25)
- runtimeErrorCount: 0.0 (n=25)
- latencyMsP50: 1339.827913 (n=25, 95% CI [660.69766, 7540.954841] (percentile-bootstrap))
- latencyMsP95: 19531.171842 (n=25, 95% CI [9562.521391, 30566.065119] (percentile-bootstrap))
- latencyMsP99: 28054.972713 (n=25, 95% CI [15322.650514, 30566.065119] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

## Slice metrics

### search
- budget-structured: n=8, casePass=True, constraintsZero=True, providerComplete=True
- category-brand-comparison: n=4, casePass=True, constraintsZero=True, providerComplete=True
- chinese-synonym-oral: n=10, casePass=True, constraintsZero=True, providerComplete=True
- exact-model-number-brand: n=10, casePass=True, constraintsZero=True, providerComplete=True
- fallback-partial-provider: n=4, casePass=True, constraintsZero=True, providerComplete=True
- negative-exclusion: n=8, casePass=True, constraintsZero=True, providerComplete=True
- no-result-conflict: n=6, casePass=True, constraintsZero=True, providerComplete=True

## Repeated Agent evidence

- k=8; pass-power=0.96
- critical workflow pass power=0.8333333333333334
- duplicate side effects=0
- state diff match rate=0.985

## RAG semantic shadow judge

- cases=50; available=50; unavailable=0; disagreements=1
- Shadow diagnostic only; it is not human ground truth and does not enter hard gates.


## Interpretation boundary

- All quality gates are domain hard gates; no weighted aggregate can hide a failure.
- Every executed case must be PASSED; an individual FAILED or ERROR case fails its domain gate.
- Provider or dependency absence is an execution failure, never a skip or pass.
- Latency is LOCAL_FULL_STACK evidence and is not a production SLO.
- P99 is descriptive when its eligible sample count is below 100.
