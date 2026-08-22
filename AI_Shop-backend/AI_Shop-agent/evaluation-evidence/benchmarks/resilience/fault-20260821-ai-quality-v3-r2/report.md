# AI Shop Fault-injection recovery matrix

- Package: `fault-20260821-ai-quality-v3-r2`
- Source run: `fault-20260821-ai-quality-v3-r2`
- Source run SHA256SUMS: `dd23ff04110b642449b4416cceafb30acd05fb5f1e0c40e8d8ac4be73bb7172a`
- Scope: `resilience` diagnostics
- Normal Search/RAG/Agent quality denominator: **excluded**
- Shadow-only signal: **no**

## Source run report

# AI Shop AI evaluation

- Run: fault-20260821-ai-quality-v3-r2
- Split: development
- Dataset SHA-256: 704f1fce4a1303a5358646a8b5cad87e2f5f08a67a08e5bf7bf7e4e04faab450
- Execution mode: LOCAL_FULL_STACK
- Overall gate: PASS

## Domain gates

- search: PASS
- rag: PASS
- agent: PASS

## Metrics

### search

### rag

### agent

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
