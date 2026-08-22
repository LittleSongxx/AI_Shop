# AI Shop AI evaluation

- Run: final-20260821-ai-quality-v4
- Split: final
- Dataset SHA-256: b4331e6a7c243fef3d28b3c7e9f7ed3b9a7724cd3bc6c72137dd52ce8f243a6a
- Execution mode: LOCAL_FULL_STACK
- Overall gate: FAIL

## Domain gates

- search: FAIL
- rag: FAIL
- agent: FAIL

## Metrics

### search
- executionRate: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- recallAt3: 0.848485 (n=44, 95% CI [0.74233, 0.943182] (percentile-bootstrap))
- recallAt5: 0.882576 (n=44, 95% CI [0.787879, 0.962216] (percentile-bootstrap))
- recallAt10: 0.882576 (n=44, 95% CI [0.784091, 0.962121] (percentile-bootstrap))
- mrrAt10: 0.835227 (n=44, 95% CI [0.744318, 0.920455] (percentile-bootstrap))
- ndcgAt5: 0.836284 (n=44, 95% CI [0.733574, 0.916242] (percentile-bootstrap))
- ndcgAt10: 0.836284 (n=44, 95% CI [0.734888, 0.917749] (percentile-bootstrap))
- noResultAccuracy: 0.94 (n=50, 95% CI [0.837829, 0.979385] (wilson))
- constraintViolationCount: 0.0 (n=50)
- hardConstraintSatisfaction: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- providerCompleteness: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- runtimeErrorCount: 0.0 (n=50)
- latencyMsP50: 354.844966 (n=50, 95% CI [209.20731, 435.40136] (percentile-bootstrap))
- latencyMsP95: 1053.785109 (n=50, 95% CI [629.345894, 1961.510139] (percentile-bootstrap))
- latencyMsP99: 1922.401438 (n=50, 95% CI [778.717137, 2401.483026] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

### rag
- executionRate: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- retrievalRecallAt3: 1.0 (n=29, 95% CI [1.0, 1.0] (percentile-bootstrap))
- retrievalRecallAt5: 1.0 (n=29, 95% CI [1.0, 1.0] (percentile-bootstrap))
- retrievalMrrAt10: 0.977011 (n=29, 95% CI [0.931034, 1.0] (percentile-bootstrap))
- retrievalNdcgAt5: 0.982759 (n=29, 95% CI [0.948276, 1.0] (percentile-bootstrap))
- sourcePrecision: 0.87931 (n=29, 95% CI [0.758621, 0.982759] (percentile-bootstrap))
- sourceCoverage: 0.896552 (n=29, 95% CI [0.758621, 1.0] (percentile-bootstrap))
- generationCorrectness: 0.86 (n=50, 95% CI [0.738138, 0.930492] (wilson))
- requiredClaimCompleteness: 0.88 (n=50, 95% CI [0.78, 0.96] (percentile-bootstrap))
- citationSupport: 0.86 (n=50, 95% CI [0.76, 0.94] (percentile-bootstrap))
- groundedFaithfulness: 1.0 (n=50, 95% CI [1.0, 1.0] (percentile-bootstrap))
- noAnswerAccuracy: 0.96 (n=50, 95% CI [0.865399, 0.988961] (wilson))
- injectionResistance: 0.5 (n=8, 95% CI [0.215216, 0.784784] (wilson))
- invalidCitationCount: 0.0 (n=50)
- severeSafetyViolationCount: 0.0 (n=50)
- providerCompleteness: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- queryExpansionFailureCount: 3.0 (n=50)
- runtimeErrorCount: 0.0 (n=50)
- latencyMsP50: 1804.557249 (n=50, 95% CI [1654.198646, 2248.69753] (percentile-bootstrap))
- latencyMsP95: 4284.17779 (n=50, 95% CI [2998.477282, 4383.346613] (percentile-bootstrap))
- latencyMsP99: 4382.933008 (n=50, 95% CI [3797.197131, 4387.999668] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

### agent
- executionRate: 0.96 (n=25, 95% CI [0.804559, 0.992904] (wilson))
- taskSuccess: 0.75 (n=24, 95% CI [0.551006, 0.880006] (wilson))
- executionCompleteness: 0.958333 (n=24, 95% CI [0.797582, 0.992607] (wilson))
- toolSelectionAccuracy: 0.791667 (n=24, 95% CI [0.595295, 0.907552] (wilson))
- toolArgumentAccuracy: 0.833333 (n=24, 95% CI [0.641469, 0.933213] (wilson))
- terminalStateCorrectness: 1.0 (n=24, 95% CI [0.862024, 1.0] (wilson))
- retryIdempotency: 1.0 (n=21, 95% CI [0.845361, 1.0] (wilson))
- stateDiffMatch: 0.958333 (n=24, 95% CI [0.797582, 0.992607] (wilson))
- duplicateSideEffectCount: 0.0 (n=24)
- providerCompleteness: 0.875 (n=24, 95% CI [0.689961, 0.956557] (wilson))
- severeSafetyViolationCount: 0.0 (n=24)
- runtimeErrorCount: 1.0 (n=25)
- latencyMsP50: 10290.694395 (n=24, 95% CI [2653.918122, 16904.983651] (percentile-bootstrap))
- latencyMsP95: 32961.783498 (n=24, 95% CI [17597.747911, 35129.401696] (percentile-bootstrap))
- latencyMsP99: 35036.122103 (n=24, 95% CI [21143.307107, 35129.401696] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

## Slice metrics

### search
- budget-structured: n=8, casePass=False, constraintsZero=True, providerComplete=True
- category-brand-comparison: n=4, casePass=False, constraintsZero=True, providerComplete=True
- chinese-synonym-oral: n=10, casePass=True, constraintsZero=True, providerComplete=True
- exact-model-number-brand: n=10, casePass=True, constraintsZero=True, providerComplete=True
- fallback-partial-provider: n=4, casePass=True, constraintsZero=True, providerComplete=True
- negative-exclusion: n=8, casePass=False, constraintsZero=True, providerComplete=True
- no-result-conflict: n=6, casePass=True, constraintsZero=True, providerComplete=True

## Repeated Agent evidence

- k=8; pass-power=0.72
- critical workflow pass power=0.0
- duplicate side effects=0
- state diff match rate=0.92

## RAG semantic shadow judge

- cases=50; available=50; unavailable=0; disagreements=7
- Shadow diagnostic only; it is not human ground truth and does not enter hard gates.


## Interpretation boundary

- All quality gates are domain hard gates; no weighted aggregate can hide a failure.
- Every executed case must be PASSED; an individual FAILED or ERROR case fails its domain gate.
- Provider or dependency absence is an execution failure, never a skip or pass.
- Latency is LOCAL_FULL_STACK evidence and is not a production SLO.
- P99 is descriptive when its eligible sample count is below 100.
