# AI Shop Repeated Agent pass^k

- Package: `agent-pass5-regression-v9-20260822`
- Source run: `regression-agent-pass5-v9-20260822`
- Source run SHA256SUMS: `acabcb0633be637360b12c91c13d4bf8c36bd2bba1892c2488a0b7d65836fc66`
- Scope: `repeated-agent` diagnostics
- Normal Search/RAG/Agent quality denominator: **excluded**
- Shadow-only signal: **yes**

## Source run report

# AI Shop AI evaluation

- Run: regression-agent-pass5-v9-20260822
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
- latencyMsP50: 626.26897 (n=5, 95% CI [24.7896, 11555.687648] (percentile-bootstrap))
- latencyMsP95: 9506.831016 (n=5, 95% CI [593.611894, 11555.687648] (percentile-bootstrap))
- latencyMsP99: 11145.916322 (n=5, 95% CI [619.737555, 11555.687648] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

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
