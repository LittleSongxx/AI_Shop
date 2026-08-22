# AI Shop AI evaluation

- Run: final-20260822-ai-quality-v5
- Split: final
- Dataset SHA-256: c9c88ee551c89605e2bda26b2b55bc1ce7d2d9ffb9b097b4330e24e2509942d0
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
- latencyMsP50: 323.696331 (n=50, 95% CI [173.536449, 373.703702] (percentile-bootstrap))
- latencyMsP95: 582.716514 (n=50, 95% CI [494.405076, 637.692801] (percentile-bootstrap))
- latencyMsP99: 636.768721 (n=50, 95% CI [555.191527, 648.088697] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

### rag
- executionRate: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- retrievalRecallAt3: 1.0 (n=29, 95% CI [1.0, 1.0] (percentile-bootstrap))
- retrievalRecallAt5: 1.0 (n=29, 95% CI [1.0, 1.0] (percentile-bootstrap))
- retrievalMrrAt10: 0.977011 (n=29, 95% CI [0.931034, 1.0] (percentile-bootstrap))
- retrievalNdcgAt5: 0.982759 (n=29, 95% CI [0.948276, 1.0] (percentile-bootstrap))
- sourcePrecision: 0.965517 (n=29, 95% CI [0.896552, 1.0] (percentile-bootstrap))
- sourceCoverage: 0.965517 (n=29, 95% CI [0.896552, 1.0] (percentile-bootstrap))
- generationCorrectness: 0.94 (n=50, 95% CI [0.837829, 0.979385] (wilson))
- requiredClaimCompleteness: 0.94 (n=50, 95% CI [0.8795, 1.0] (percentile-bootstrap))
- citationSupport: 0.94 (n=50, 95% CI [0.86, 1.0] (percentile-bootstrap))
- groundedFaithfulness: 1.0 (n=50, 95% CI [1.0, 1.0] (percentile-bootstrap))
- noAnswerAccuracy: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- injectionResistance: 0.5 (n=8, 95% CI [0.215216, 0.784784] (wilson))
- invalidCitationCount: 0.0 (n=50)
- severeSafetyViolationCount: 0.0 (n=50)
- providerCompleteness: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- queryExpansionFailureCount: 3.0 (n=50)
- runtimeErrorCount: 0.0 (n=50)
- latencyMsP50: 1993.984968 (n=50, 95% CI [1458.983628, 2370.490746] (percentile-bootstrap))
- latencyMsP95: 4127.774495 (n=50, 95% CI [2803.810519, 4337.841866] (percentile-bootstrap))
- latencyMsP99: 4333.986598 (n=50, 95% CI [3450.481069, 4381.213631] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

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
- latencyMsP50: 1255.440822 (n=25, 95% CI [1034.587708, 6050.781757] (percentile-bootstrap))
- latencyMsP95: 14676.809749 (n=25, 95% CI [9136.048103, 16832.402567] (percentile-bootstrap))
- latencyMsP99: 16444.736841 (n=25, 95% CI [11026.744462, 16832.402567] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

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
- state diff match rate=0.965

## RAG semantic shadow judge

- cases=50; available=49; unavailable=1; disagreements=2
- Shadow diagnostic only; it is not human ground truth and does not enter hard gates.


## Interpretation boundary

- All quality gates are domain hard gates; no weighted aggregate can hide a failure.
- Every executed case must be PASSED; an individual FAILED or ERROR case fails its domain gate.
- Provider or dependency absence is an execution failure, never a skip or pass.
- Latency is LOCAL_FULL_STACK evidence and is not a production SLO.
- P99 is descriptive when its eligible sample count is below 100.
