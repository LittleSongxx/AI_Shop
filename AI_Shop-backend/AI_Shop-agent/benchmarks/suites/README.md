# Evaluation suite contracts

All new formal runs use the single public `benchmarks/eval.py` lifecycle. These contracts isolate post-fix suites from retained historical evidence.
Formal runs use a new directory and a run ID containing the suite, code commit,
date, and optional qualifier. Existing run directories are immutable.

- Search: `search-v3-<git-sha>-<yyyymmdd>[-qualifier]`
- RAG: `rag-v5-<git-sha>-<yyyymmdd>[-qualifier]`
- Agent: `agent-v2-<mode>-<git-sha>-<yyyymmdd>[-qualifier]`

A fresh dataset is single-use. Once a formal run opens it, a failed result is
retained and the data moves to known regression. A repaired implementation must
use the next suite version and a newly locked holdout. `--accept-baseline` is not
an allowed way to turn a failed formal run into a pass.

The executable contracts all dispatch through one public entrypoint:

- `search-v3.json` -> `benchmarks/eval.py` (`adapter=search-v3`), with 240 + 45 known
  observations and one-shot 80 fresh + 40 challenge + 30 ProductService cases.
- `rag-v5.json` -> `benchmarks/eval.py` (`adapter=rag-v5`), internally reusing the
  retrieval/generation domain modules, pinned to catalog v2 and an immutable Java
  knowledge release. Retrieval uses 264 known + 48 fresh; generation uses 60 known + 20 fresh.
- `agent-v2.json` -> `benchmarks/eval.py` (`adapter=agent-v2`), internally reusing
  the real-stack evaluator, with 37 inherited single-turn tasks and 7 stateful
  recommendation/commerce/support sequences.

The older scripts are implementation compatibility modules only; see
`benchmarks/LEGACY.md`. They are not parallel formal lifecycles.

Search and RAG write an atomic execution claim before touching a fresh set.
Provider failure, fallback, partial execution, changed SHA, or a second run ID is
a hard failure. Generation creates a fresh-only blind package; its status remains
`HUMAN_REVIEW_PENDING` until two different real reviewer IDs submit all four
dimensions for all 20 cases.
