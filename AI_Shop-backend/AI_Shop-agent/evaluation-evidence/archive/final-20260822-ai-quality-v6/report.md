# AI Shop AI evaluation

- Run: final-20260822-ai-quality-v6
- Split: final
- Dataset SHA-256: a17508fd3c6a87f317defed56dbd1f4e4dafab24e2f8de451725df4384504c17
- Execution mode: LOCAL_FULL_STACK
- Overall gate: FAIL

## Domain gates

- search: PASS
- rag: FAIL
- agent: FAIL

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
- latencyMsP50: 253.738148 (n=50, 95% CI [185.255472, 333.930069] (percentile-bootstrap))
- latencyMsP95: 578.85675 (n=50, 95% CI [493.141693, 866.568876] (percentile-bootstrap))
- latencyMsP99: 861.013222 (n=50, 95% CI [551.781299, 929.069985] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

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
- groundedFaithfulness: 1.0 (n=50, 95% CI [1.0, 1.0] (percentile-bootstrap))
- noAnswerAccuracy: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- injectionResistance: 1.0 (n=8, 95% CI [0.675592, 1.0] (wilson))
- invalidCitationCount: 0.0 (n=50)
- severeSafetyViolationCount: 0.0 (n=50)
- providerCompleteness: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- queryExpansionFailureCount: 3.0 (n=50)
- runtimeErrorCount: 0.0 (n=50)
- latencyMsP50: 1654.070243 (n=50, 95% CI [1430.354578, 1882.770505] (percentile-bootstrap))
- latencyMsP95: 3793.762063 (n=50, 95% CI [2445.936203, 4401.011597] (percentile-bootstrap))
- latencyMsP99: 4400.086309 (n=50, 95% CI [3080.070218, 4411.421091] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

### agent
- executionRate: 0.96 (n=25, 95% CI [0.804559, 0.992904] (wilson))
- taskSuccess: 0.958333 (n=24, 95% CI [0.797582, 0.992607] (wilson))
- executionCompleteness: 0.958333 (n=24, 95% CI [0.797582, 0.992607] (wilson))
- toolSelectionAccuracy: 0.958333 (n=24, 95% CI [0.797582, 0.992607] (wilson))
- toolArgumentAccuracy: 1.0 (n=24, 95% CI [0.862024, 1.0] (wilson))
- terminalStateCorrectness: 1.0 (n=24, 95% CI [0.862024, 1.0] (wilson))
- retryIdempotency: 1.0 (n=21, 95% CI [0.845361, 1.0] (wilson))
- stateDiffMatch: 1.0 (n=24, 95% CI [0.862024, 1.0] (wilson))
- duplicateSideEffectCount: 0.0 (n=24)
- providerCompleteness: 1.0 (n=24, 95% CI [0.862024, 1.0] (wilson))
- severeSafetyViolationCount: 0.0 (n=24)
- runtimeErrorCount: 1.0 (n=25)
- latencyMsP50: 1290.182377 (n=24, 95% CI [836.572213, 2240.056608] (percentile-bootstrap))
- latencyMsP95: 16945.314252 (n=24, 95% CI [7934.264277, 43191.073994] (percentile-bootstrap))
- latencyMsP99: 37223.473988 (n=24, 95% CI [13565.200623, 43191.073994] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

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

- k=8; pass-power=0.92
- critical workflow pass power=0.8333333333333334
- duplicate side effects=0
- state diff match rate=0.99

## RAG semantic shadow judge

- cases=50; available=50; unavailable=0; disagreements=1
- Shadow diagnostic only; it is not human ground truth and does not enter hard gates.


## Interpretation boundary

- All quality gates are domain hard gates; no weighted aggregate can hide a failure.
- Every executed case must be PASSED; an individual FAILED or ERROR case fails its domain gate.
- Provider or dependency absence is an execution failure, never a skip or pass.
- Latency is LOCAL_FULL_STACK evidence and is not a production SLO.
- P99 is descriptive when its eligible sample count is below 100.
