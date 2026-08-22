# AI Shop Repeated Agent pass^k

- Package: `agent-pass5-v6-r2-20260822`
- Source run: `visible-agent-development-pass5-v6-r2-20260822`
- Source run SHA256SUMS: `6aa107519b3bb4cd329303fc6da0fb215967f3d7b234bae1da4e8447daaa7116`
- Scope: `repeated-agent` diagnostics
- Normal Search/RAG/Agent quality denominator: **excluded**
- Shadow-only signal: **yes**

## Source run report

# AI Shop AI evaluation

- Run: visible-agent-development-pass5-v6-r2-20260822
- Split: development
- Dataset SHA-256: 01cbf2996f9d0ba7b47503dc748b6dcc18374d7cdcb7404c5df22006b67ffa50
- Execution mode: LOCAL_FULL_STACK
- Overall gate: PASS

## Domain gates

- agent: PASS

## Metrics

### agent
- executionRate: 1.0 (n=7, 95% CI [0.64567, 1.0] (wilson))
- taskSuccess: 1.0 (n=7, 95% CI [0.64567, 1.0] (wilson))
- executionCompleteness: 1.0 (n=7, 95% CI [0.64567, 1.0] (wilson))
- toolSelectionAccuracy: 1.0 (n=7, 95% CI [0.64567, 1.0] (wilson))
- toolArgumentAccuracy: 1.0 (n=7, 95% CI [0.64567, 1.0] (wilson))
- terminalStateCorrectness: 1.0 (n=7, 95% CI [0.64567, 1.0] (wilson))
- retryIdempotency: 1.0 (n=5, 95% CI [0.565518, 1.0] (wilson))
- stateDiffMatch: 1.0 (n=7, 95% CI [0.64567, 1.0] (wilson))
- duplicateSideEffectCount: 0.0 (n=7)
- providerCompleteness: 1.0 (n=7, 95% CI [0.64567, 1.0] (wilson))
- severeSafetyViolationCount: 0.0 (n=7)
- runtimeErrorCount: 0.0 (n=7)
- latencyMsP50: 619.619002 (n=7, 95% CI [24.970891, 1748.90961] (percentile-bootstrap))
- latencyMsP95: 6352.550361 (n=7, 95% CI [869.311641, 8325.539255] (percentile-bootstrap))
- latencyMsP99: 7930.941476 (n=7, 95% CI [1007.400748, 8325.539255] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

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
