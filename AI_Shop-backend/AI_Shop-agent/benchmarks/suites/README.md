# Versioned evaluation suites

These contracts isolate the post-fix suites from retained historical evidence.
Formal runs use a new directory and a run ID containing the suite, code commit,
date, and optional qualifier. Existing run directories are immutable.

- Search: `search-v3-<git-sha>-<yyyymmdd>[-qualifier]`
- RAG: `rag-v5-<git-sha>-<yyyymmdd>[-qualifier]`
- Agent: `agent-v2-<mode>-<git-sha>-<yyyymmdd>[-qualifier]`

A fresh dataset is single-use. Once a formal run opens it, a failed result is
retained and the data moves to known regression. A repaired implementation must
use the next suite version and a newly locked holdout. `--accept-baseline` is not
an allowed way to turn a failed formal run into a pass.

The executable contracts are:

- `search-v3.json` -> `benchmarks/run_search_v3_eval.py`, with 240 + 45 known
  observations and one-shot 80 fresh + 40 challenge + 30 ProductService cases.
- `rag-v5.json` -> `benchmarks/run_rag_v5_eval.py` and
  `benchmarks/run_rag_generation_v5.py`, pinned to catalog v2 and an immutable
  Java knowledge release. Retrieval uses 264 known + 48 fresh; generation uses
  60 known + 20 fresh.
- `agent-v2.json` -> `benchmarks/run_task_success_v2_eval.py`, with 37 inherited
  single-turn tasks and 7 stateful recommendation/commerce/support sequences.

Search and RAG write an atomic execution claim before touching a fresh set.
Provider failure, fallback, partial execution, changed SHA, or a second run ID is
a hard failure. Generation creates a fresh-only blind package; its status remains
`HUMAN_REVIEW_PENDING` until two different real reviewer IDs submit all four
dimensions for all 20 cases.
