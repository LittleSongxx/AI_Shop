# AI Shop Repeated Agent pass^k

- Package: `repeat-20260821-ai-quality-v3`
- Source run: `repeat-20260821-ai-quality-v3`
- Source run SHA256SUMS: `11c14c9ff9cb167b843822ca7c77775b169883200d61ac82751aaae4745ba9b1`
- Scope: `repeated-agent` diagnostics
- Normal Search/RAG/Agent quality denominator: **excluded**
- Shadow-only signal: **no**

## Source run report

# AI Shop AI evaluation

- Run: repeat-20260821-ai-quality-v3
- Split: development
- Dataset SHA-256: 737c8f1b6107811b1804c361135be6cfedc14974ef0567a8944fb37b705f4ac9
- Execution mode: LOCAL_FULL_STACK
- Overall gate: FAIL

## Domain gates

- agent: FAIL

## Metrics

### agent

## Repeated Agent evidence

- k=5; pass-power=1.0
- critical workflow pass power=0.0
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
