# AI Shop Repeated Agent pass^k

- Package: `agent-pass5-regression-v4-r15`
- Source run: `visible-agent-regression-pass5-v4-r15-20260821`
- Source run SHA256SUMS: `3bfa2db702bfb6c25e29e7ccb99d1178023db0d7bc7688a9fc86fe06cf7890ad`
- Scope: `repeated-agent` diagnostics
- Normal Search/RAG/Agent quality denominator: **excluded**
- Shadow-only signal: **yes**

## Source run report

# AI Shop AI evaluation

- Run: visible-agent-regression-pass5-v4-r15-20260821
- Split: regression
- Dataset SHA-256: a102ed7a7e6225ad52da9a60c05c25f8c9f22a2ed5f036395b00c906e802eeb2
- Execution mode: LOCAL_FULL_STACK
- Overall gate: PASS

## Domain gates

- agent: PASS

## Metrics

### agent
- executionRate: 1.0 (n=5, 95% CI [0.565518, 1.0] (wilson))
- taskSuccess: 1.0 (n=5, 95% CI [0.565518, 1.0] (wilson))
- executionCompleteness: 1.0 (n=5, 95% CI [0.565518, 1.0] (wilson))
- toolSelectionAccuracy: 1.0 (n=5, 95% CI [0.565518, 1.0] (wilson))
- toolArgumentAccuracy: 1.0 (n=5, 95% CI [0.565518, 1.0] (wilson))
- terminalStateCorrectness: 1.0 (n=5, 95% CI [0.565518, 1.0] (wilson))
- retryIdempotency: 1.0 (n=4, 95% CI [0.510109, 1.0] (wilson))
- stateDiffMatch: 1.0 (n=5, 95% CI [0.565518, 1.0] (wilson))
- duplicateSideEffectCount: 0.0 (n=5)
- providerCompleteness: 1.0 (n=5, 95% CI [0.565518, 1.0] (wilson))
- severeSafetyViolationCount: 0.0 (n=5)
- runtimeErrorCount: 0.0 (n=5)
- latencyMsP50: 1637.186567 (n=5, 95% CI [31.326982, 27025.076774] (percentile-bootstrap))
- latencyMsP95: 23854.48752 (n=5, 95% CI [1403.301357, 27025.076774] (percentile-bootstrap))
- latencyMsP99: 26390.958923 (n=5, 95% CI [1590.409525, 27025.076774] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

## Repeated Agent evidence

- k=5; pass-power=1.0
- critical workflow pass power=1.0
- duplicate side effects=0
- state diff match rate=1.0

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
