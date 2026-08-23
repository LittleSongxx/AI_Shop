# 客服槽位同集 Paired Replay

> `customer-service-slot-replay-v1-20260823`；相同 60 条人工金标，只隔离生产规则变化；不作为新 holdout。

| 指标 | 优化前 | 优化后 | 绝对变化 | 修复 / 残余 / 回归 |
|---|---:|---:|---:|---|
| `slotEntitySpanF1` | 0.907652 | 0.996364 | 0.088712 | 12 / 3 / 0 |
| `slotExactMatch` | 0.558824 | 0.911765 | 0.352941 | 12 / 3 / 0 |

残余 strict-format badcase：`cs-gold-v1-009, cs-gold-v1-020, cs-gold-v1-058`。

边界：该结果证明当前规则在同一人工金标上的改善，不证明未见请求、线上客服成功率或业务转化。
