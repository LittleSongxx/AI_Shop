# AI Shop Fault-injection recovery matrix

- Package: `fault-20260821-ai-quality-v3-finalmatrix-r2`
- Source run: `fault-20260821-ai-quality-v3-finalmatrix-r2`
- Source run SHA256SUMS: `e13bc29a1e2baaba389903d8b275b7e593f7beab68a5f298386a85a782146e59`
- Scope: `resilience` diagnostics
- Normal Search/RAG/Agent quality denominator: **excluded**
- Shadow-only signal: **no**

## Source run report

# AI Shop AI evaluation

- Run: fault-20260821-ai-quality-v3-finalmatrix-r2
- Split: development
- Dataset SHA-256: 2e08ebed7f4528f6a299d87a9cbcec988793822acf6e5445a84870a6d6ce8881
- Execution mode: LOCAL_FULL_STACK
- Overall gate: PASS

## Domain gates

- search: PASS
- rag: PASS
- agent: PASS

## Metrics

### search
- executionRate: 1.0 (n=7, 95% CI [0.64567, 1.0] (wilson))
- recallAt3: 0.714286 (n=7, 95% CI [0.428571, 1.0] (percentile-bootstrap))
- recallAt5: 0.714286 (n=7, 95% CI [0.428571, 1.0] (percentile-bootstrap))
- recallAt10: 0.714286 (n=7, 95% CI [0.428571, 1.0] (percentile-bootstrap))
- mrrAt10: 0.714286 (n=7, 95% CI [0.428571, 1.0] (percentile-bootstrap))
- ndcgAt5: 0.670046 (n=7, 95% CI [0.362211, 0.95576] (percentile-bootstrap))
- ndcgAt10: 0.670046 (n=7, 95% CI [0.285714, 0.95576] (percentile-bootstrap))
- noResultAccuracy: 0.714286 (n=7, 95% CI [0.358934, 0.917781] (wilson))
- constraintViolationCount: 0.0 (n=7)
- hardConstraintSatisfaction: 1.0 (n=7, 95% CI [0.64567, 1.0] (wilson))
- providerCompleteness: 0.857143 (n=7, 95% CI [0.486872, 0.97432] (wilson))
- runtimeErrorCount: 0.0 (n=7)
- latencyMsP50: 459.704319 (n=7, 95% CI [162.427566, 559.157881] (percentile-bootstrap))
- latencyMsP95: 567.344817 (n=7, 95% CI [459.704319, 570.853504] (percentile-bootstrap))
- latencyMsP99: 570.151767 (n=7, 95% CI [535.540718, 570.853504] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

### rag
- executionRate: 0.0 (n=1, 95% CI [0.0, 0.793451] (wilson))
- retrievalRecallAt3: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- retrievalRecallAt5: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- retrievalMrrAt10: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- retrievalNdcgAt5: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- sourcePrecision: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- sourceCoverage: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- generationCorrectness: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- requiredClaimCompleteness: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- citationSupport: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- groundedFaithfulness: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- noAnswerAccuracy: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- injectionResistance: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- invalidCitationCount: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- severeSafetyViolationCount: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- providerCompleteness: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- queryExpansionFailureCount: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- runtimeErrorCount: 1.0 (n=1)
- latencyMsP50: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- latencyMsP95: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- latencyMsP99: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)

### agent
- executionRate: 0.25 (n=4, 95% CI [0.045587, 0.699358] (wilson))
- taskSuccess: 0.0 (n=1, 95% CI [0.0, 0.793451] (wilson))
- executionCompleteness: 1.0 (n=1, 95% CI [0.206549, 1.0] (wilson))
- toolSelectionAccuracy: 1.0 (n=1, 95% CI [0.206549, 1.0] (wilson))
- toolArgumentAccuracy: 1.0 (n=1, 95% CI [0.206549, 1.0] (wilson))
- terminalStateCorrectness: 1.0 (n=1, 95% CI [0.206549, 1.0] (wilson))
- retryIdempotency: 0.0 (n=1, 95% CI [0.0, 0.793451] (wilson))
- stateDiffMatch: 1.0 (n=1, 95% CI [0.206549, 1.0] (wilson))
- duplicateSideEffectCount: 0.0 (n=1)
- providerCompleteness: 1.0 (n=1, 95% CI [0.206549, 1.0] (wilson))
- severeSafetyViolationCount: 0.0 (n=1)
- runtimeErrorCount: 3.0 (n=4)
- latencyMsP50: 60571.781058 (n=1, 95% CI [60571.781058, 60571.781058] (percentile-bootstrap))
- latencyMsP95: 60571.781058 (n=1, 95% CI [60571.781058, 60571.781058] (percentile-bootstrap))
- latencyMsP99: 60571.781058 (n=1, 95% CI [60571.781058, 60571.781058] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

## Interpretation boundary

- All quality gates are domain hard gates; no weighted aggregate can hide a failure.
- Every executed case must be PASSED; an individual FAILED or ERROR case fails its domain gate.
- Provider or dependency absence is an execution failure, never a skip or pass.
- Latency is LOCAL_FULL_STACK evidence and is not a production SLO.
- P99 is descriptive when its eligible sample count is below 100.

## Evidence boundary

- This package preserves diagnostic evidence and does not alter historical final scores.
- Fault-injection outcomes are recovery-contract results, not normal quality passes.
- pass^k is a repeated-task reliability estimate for this run and case set, not a production SLO.
- Local timings, usage, and costs retain their original unknown/unpriced states.
