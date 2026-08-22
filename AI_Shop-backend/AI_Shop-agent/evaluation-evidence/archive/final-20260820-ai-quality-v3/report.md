# AI Shop AI evaluation

- Run: final-20260820-ai-quality-v3
- Split: final
- Dataset SHA-256: 296ed448fac4c4ad9c2373a853448b87cb046535e65387ba08ab5a007fdc02c1
- Execution mode: LOCAL_FULL_STACK
- Overall gate: FAIL

## Domain gates

- search: FAIL
- rag: FAIL
- agent: FAIL

## Metrics

### search
- executionRate: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- recallAt3: 0.857724 (n=41, 95% CI [0.756098, 0.943089] (percentile-bootstrap))
- recallAt5: 0.906504 (n=41, 95% CI [0.813008, 0.979675] (percentile-bootstrap))
- recallAt10: 0.906504 (n=41, 95% CI [0.817073, 0.979675] (percentile-bootstrap))
- mrrAt10: 0.870732 (n=41, 95% CI [0.773171, 0.95122] (percentile-bootstrap))
- ndcgAt5: 0.841278 (n=41, 95% CI [0.745274, 0.923721] (percentile-bootstrap))
- ndcgAt10: 0.841278 (n=41, 95% CI [0.745955, 0.921309] (percentile-bootstrap))
- noResultAccuracy: 0.88 (n=50, 95% CI [0.761952, 0.943824] (wilson))
- constraintViolationCount: 0.0 (n=50)
- hardConstraintSatisfaction: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- providerCompleteness: 0.98 (n=50, 95% CI [0.895046, 0.996461] (wilson))
- runtimeErrorCount: 0.0 (n=50)
- latencyMsP50: 437.568607 (n=50, 95% CI [322.157389, 494.152911] (percentile-bootstrap))
- latencyMsP95: 717.752131 (n=50, 95% CI [568.303371, 1770.776997] (percentile-bootstrap))
- latencyMsP99: 1703.239861 (n=50, 95% CI [655.603592, 2530.569778] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

### rag
- executionRate: 1.0 (n=50, 95% CI [0.928652, 1.0] (wilson))
- retrievalRecallAt3: 0.941176 (n=34, 95% CI [0.852941, 1.0] (percentile-bootstrap))
- retrievalRecallAt5: 0.970588 (n=34, 95% CI [0.911765, 1.0] (percentile-bootstrap))
- retrievalMrrAt10: 0.917647 (n=34, 95% CI [0.829412, 0.985294] (percentile-bootstrap))
- retrievalNdcgAt5: 0.941699 (n=34, 95% CI [0.864319, 1.0] (percentile-bootstrap))
- sourcePrecision: 0.808824 (n=34, 95% CI [0.661765, 0.926471] (percentile-bootstrap))
- sourceCoverage: 0.823529 (n=34, 95% CI [0.676471, 0.941176] (percentile-bootstrap))
- generationCorrectness: 0.8 (n=50, 95% CI [0.669629, 0.887562] (wilson))
- requiredClaimCompleteness: 0.82 (n=50, 95% CI [0.7, 0.92] (percentile-bootstrap))
- citationSupport: 0.8 (n=50, 95% CI [0.68, 0.9] (percentile-bootstrap))
- groundedFaithfulness: 0.989333 (n=50, 95% CI [0.972, 1.0] (percentile-bootstrap))
- noAnswerAccuracy: 0.88 (n=50, 95% CI [0.761952, 0.943824] (wilson))
- injectionResistance: 0.5 (n=8, 95% CI [0.215216, 0.784784] (wilson))
- invalidCitationCount: 0.0 (n=50)
- severeSafetyViolationCount: 0.0 (n=50)
- providerCompleteness: 0.92 (n=50, 95% CI [0.811618, 0.96845] (wilson))
- queryExpansionFailureCount: 2.0 (n=50)
- runtimeErrorCount: 0.0 (n=50)
- latencyMsP50: 7369.801316 (n=50, 95% CI [5601.600975, 8650.96056] (percentile-bootstrap))
- latencyMsP95: 24395.700356 (n=50, 95% CI [14439.740852, 30312.050031] (percentile-bootstrap))
- latencyMsP99: 30168.226435 (n=50, 95% CI [20179.281279, 31930.065487] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

### agent
- executionRate: 0.96 (n=25, 95% CI [0.804559, 0.992904] (wilson))
- taskSuccess: 0.708333 (n=24, 95% CI [0.508323, 0.850854] (wilson))
- executionCompleteness: 0.791667 (n=24, 95% CI [0.595295, 0.907552] (wilson))
- toolSelectionAccuracy: 1.0 (n=24, 95% CI [0.862024, 1.0] (wilson))
- toolArgumentAccuracy: 1.0 (n=24, 95% CI [0.862024, 1.0] (wilson))
- terminalStateCorrectness: 0.916667 (n=24, 95% CI [0.741512, 0.976841] (wilson))
- retryIdempotency: 0.904762 (n=21, 95% CI [0.710859, 0.973481] (wilson))
- stateDiffMatch: 1.0 (n=24, 95% CI [0.862024, 1.0] (wilson))
- duplicateSideEffectCount: 0.0 (n=24)
- providerCompleteness: 0.75 (n=24, 95% CI [0.551006, 0.880006] (wilson))
- severeSafetyViolationCount: 0.0 (n=24)
- runtimeErrorCount: 1.0 (n=25)
- latencyMsP50: 12250.469653 (n=24, 95% CI [6932.851587, 17691.826718] (percentile-bootstrap))
- latencyMsP95: 34698.958922 (n=24, 95% CI [19904.217661, 217194.853964] (percentile-bootstrap))
- latencyMsP99: 175263.475332 (n=24, 95% CI [27604.42851, 217194.853964] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

## Slice metrics

### search
- budget-structured: n=8, casePass=False, constraintsZero=True, providerComplete=True
- category-brand-comparison: n=4, casePass=True, constraintsZero=True, providerComplete=True
- chinese-synonym-oral: n=10, casePass=False, constraintsZero=True, providerComplete=True
- exact-model-number-brand: n=10, casePass=True, constraintsZero=True, providerComplete=True
- fallback-partial-provider: n=4, casePass=True, constraintsZero=True, providerComplete=True
- negative-exclusion: n=8, casePass=False, constraintsZero=True, providerComplete=True
- no-result-conflict: n=6, casePass=False, constraintsZero=True, providerComplete=False

## Repeated Agent evidence

- k=8; pass-power=0.6
- critical workflow pass power=0.5
- duplicate side effects=0
- state diff match rate=0.98

## RAG semantic shadow judge

- cases=50; available=0; unavailable=50; disagreements=0
- Shadow diagnostic only; it is not human ground truth and does not enter hard gates.


## Interpretation boundary

- All quality gates are domain hard gates; no weighted aggregate can hide a failure.
- Every executed case must be PASSED; an individual FAILED or ERROR case fails its domain gate.
- Provider or dependency absence is an execution failure, never a skip or pass.
- Latency is LOCAL_FULL_STACK evidence and is not a production SLO.
- P99 is descriptive when its eligible sample count is below 100.
