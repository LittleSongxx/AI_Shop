# Legacy evaluation entrypoints

The only public evaluation entrypoint is `benchmarks/eval.py`.

The scripts below remain in the repository because historical evidence and
unit tests refer to their domain helpers. They are not a second formal
lifecycle and must not be used to claim new fresh evidence:

- `run_search_v2_eval.py`, `run_search_v3_eval.py`
- `run_rag_v3_eval.py`, `run_rag_v4_eval.py`, `run_rag_v5_eval.py`
- `run_rag_generation_eval.py`, `run_rag_generation_v4.py`, `run_rag_generation_v5.py`
- `run_task_success_eval.py`, `run_task_success_v2_eval.py`
- `run_search_rag_eval.py`, `run_search_rag_mature_eval.py`
- `run_agentic_commerce_runtime.py`, `run_ai_safety.py`, `run_ablation.py`

Use them only for read-only historical replay or as implementation modules
behind the unified adapters. Do not delete a fresh execution lock, overwrite a
formal run, or use a legacy command to rescore a retained holdout.
