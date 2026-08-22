# AI Shop Repeated Agent pass^k

- Package: `repeat-20260821-ai-quality-v3-final`
- Source run: `repeat-20260821-ai-quality-v3-final`
- Source run SHA256SUMS: `00bff56d374ad53cdf95f0bd09b53efe792453928f1c11b7a23dfcbf5f94352d`
- Scope: `repeated-agent` diagnostics
- Normal Search/RAG/Agent quality denominator: **excluded**
- Shadow-only signal: **no**

## Source run report

# AI Shop AI evaluation

- Run: repeat-20260821-ai-quality-v3-final
- Split: development
- Dataset SHA-256: 737c8f1b6107811b1804c361135be6cfedc14974ef0567a8944fb37b705f4ac9
- Execution mode: LOCAL_FULL_STACK
- Overall gate: PASS

## Domain gates

- agent: PASS

## Metrics

### agent
- executionRate: 1.0 (n=6, 95% CI [0.609666, 1.0] (wilson))
- taskSuccess: 1.0 (n=6, 95% CI [0.609666, 1.0] (wilson))
- executionCompleteness: 1.0 (n=6, 95% CI [0.609666, 1.0] (wilson))
- toolSelectionAccuracy: 1.0 (n=6, 95% CI [0.609666, 1.0] (wilson))
- toolArgumentAccuracy: 1.0 (n=6, 95% CI [0.609666, 1.0] (wilson))
- terminalStateCorrectness: 1.0 (n=6, 95% CI [0.609666, 1.0] (wilson))
- retryIdempotency: 1.0 (n=4, 95% CI [0.510109, 1.0] (wilson))
- stateDiffMatch: 1.0 (n=6, 95% CI [0.609666, 1.0] (wilson))
- duplicateSideEffectCount: 0.0 (n=6)
- providerCompleteness: 1.0 (n=6, 95% CI [0.609666, 1.0] (wilson))
- severeSafetyViolationCount: 0.0 (n=6)
- runtimeErrorCount: 0.0 (n=6)
- latencyMsP50: 2492.566822 (n=6, 95% CI [33.948823, 12680.678181] (percentile-bootstrap))
- latencyMsP95: 15942.701467 (n=6, 95% CI [3509.183894, 19204.724752] (percentile-bootstrap))
- latencyMsP99: 18552.320095 (n=6, 95% CI [4322.477552, 19204.724752] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

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
