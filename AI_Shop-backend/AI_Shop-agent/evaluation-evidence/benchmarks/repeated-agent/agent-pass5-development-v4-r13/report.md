# AI Shop Repeated Agent pass^k

- Package: `agent-pass5-development-v4-r13`
- Source run: `visible-agent-development-pass5-v4-r13-20260821`
- Source run SHA256SUMS: `029fdf1251f7c0e3f6dc8cbe0c3efbb224929f7298d860251f90626d60165ad7`
- Scope: `repeated-agent` diagnostics
- Normal Search/RAG/Agent quality denominator: **excluded**
- Shadow-only signal: **yes**

## Source run report

# AI Shop AI evaluation

- Run: visible-agent-development-pass5-v4-r13-20260821
- Split: development
- Dataset SHA-256: eb7e2ec8f02ae721d3c2815bcc0ff9c1d85e7c335ab306d2b136a1e1bd56841d
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
- latencyMsP50: 2170.531108 (n=7, 95% CI [41.338434, 8780.376437] (percentile-bootstrap))
- latencyMsP95: 17065.27589 (n=7, 95% CI [4773.152903, 20615.947084] (percentile-bootstrap))
- latencyMsP99: 19905.812845 (n=7, 95% CI [6442.760814, 20615.947084] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

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
