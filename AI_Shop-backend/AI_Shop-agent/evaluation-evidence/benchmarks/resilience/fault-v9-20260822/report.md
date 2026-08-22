# AI Shop Fault-injection recovery matrix

- Package: `fault-v9-20260822`
- Source run: `resilience-v9-20260822`
- Source run SHA256SUMS: `0c664acbb8daaf41973619b713a9183a1979e81634f57b27edb1693048836d3f`
- Scope: `resilience` diagnostics
- Normal Search/RAG/Agent quality denominator: **excluded**
- Shadow-only signal: **no**

## Source run report

# AI Shop AI evaluation

- Run: resilience-v9-20260822
- Split: development
- Dataset SHA-256: 6c558af2ed7e89478268821be4eee3d1cbec4f38131b3b756035d05e6e27670d
- Execution mode: LOCAL_FULL_STACK
- Overall gate: PASS

## Domain gates

- search: PASS
- rag: PASS
- agent: PASS

## Metrics

### search
- executionRate: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- recallAt3: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- recallAt5: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- recallAt10: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- mrrAt10: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- ndcgAt5: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- ndcgAt10: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- noResultAccuracy: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- constraintViolationCount: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- hardConstraintSatisfaction: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- providerCompleteness: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- runtimeErrorCount: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- latencyMsP50: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- latencyMsP95: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- latencyMsP99: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)

### rag
- executionRate: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
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
- runtimeErrorCount: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- latencyMsP50: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- latencyMsP95: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- latencyMsP99: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)

### agent
- executionRate: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- taskSuccess: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- executionCompleteness: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- toolSelectionAccuracy: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- toolArgumentAccuracy: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- terminalStateCorrectness: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- retryIdempotency: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- stateDiffMatch: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- duplicateSideEffectCount: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- providerCompleteness: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- severeSafetyViolationCount: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- runtimeErrorCount: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- latencyMsP50: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- latencyMsP95: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)
- latencyMsP99: 0.0 (n=0, notes=NO_ELIGIBLE_SAMPLES)

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
