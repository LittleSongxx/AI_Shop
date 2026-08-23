# Search hard-negative 成对回放

> `AUXILIARY_PAIRED_REPLAY`；仅为辅助诊断，不修改 v9 final，不进入正常质量分母。

Run：`search-hard-negative-paired-v1-20260823`；case：`10`；baseline：`final-20260822-ai-quality-v9`。

| 指标 | v9 baseline | current candidate | delta |
|---|---:|---:|---:|
| `recallAt10` | 0.833333 | 0.833333 | 0.0 |
| `recallAt10Micro` | 0.777778 | 0.777778 | 0.0 |
| `mrrAt10` | 0.725 | 0.725 | 0.0 |
| `ndcgAt10` | 0.650292 | 0.650292 | 0.0 |

## Badcase

| Case | 原因 | Recall Δ | MRR Δ | NDCG Δ | 未召回 |
|---|---|---:|---:|---:|---|
| `search-fin-v9-23-snack-100` | RECALL_MISS_REMAINS | 0.0 | 0.0 | 0.0 | 303019597302892, 438316828084252 |
| `search-fin-v9-34-snack-no-wangwang` | RECALL_MISS_REMAINS | 0.0 | 0.0 | 0.0 | 303019597302892 |
| `search-fin-v9-47-compare-xm` | RECALL_MISS_REMAINS | 0.0 | 0.0 | 0.0 | 350000232815799 |

本地 P50/P95 仅描述本次完整链路回放，不是生产 SLO。每条 query、qrels hash、前后 ranking、Provider trace 与新引入负样本均保存在 `cases.jsonl`。
