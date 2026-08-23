# AI_Shop 文档与证据入口

文档按内容归档，不按日期复制新文件。后续同类变化在同一份稳定文档的“增量记录”中追加；日期只用于说明开发阶段。

| 目录/文件 | 内容 | 证据等级 |
|---|---|---|
| [project/AI_Shop主线与开发记录](project/AI_Shop主线与开发记录.md) | AI 主线、方法判断、关键改造、阶段性取舍和后续路线 | 源码与运行证据汇总 |
| [evaluation/AI质量评测与Badcase](evaluation/AI质量评测与Badcase.md) | Search/RAG/Agent 质量指标、门禁、badcase、性能和限制 | current/archive 证据包投影 |
| [evaluation/AI质量评测与Badcase.json](evaluation/AI质量评测与Badcase.json) | Search/RAG/Agent 指标、延迟诊断、usage 状态及逐指标 badcase 的机器可读结果 | immutable scorecard 投影 |
| [evaluation/customer-service/客服金标评测](evaluation/customer-service/客服金标评测.md) | 60 条双人盲标+第三人仲裁的 intent、风险、slot、handoff 质量证据 | `HUMAN_VERIFIED` 离线证据，仍不进入 release gate |
| [evaluation/customer-service/客服金标评测.json](evaluation/customer-service/客服金标评测.json) | 客服评测逐 case、canonical slot 诊断和 badcase | 可复核机器证据 |
| [evidence-manifest.json](evidence-manifest.json) | current、archive、visible run、哈希和生命周期机器索引 | 机器校验入口 |

项目根目录的 [Java 后端面试报告](../AI应用开发_Java后端_真实面试题与备考报告_20260821.md) 是独立的求职研究材料，不与项目开发日志重复合并。

## 证据目录边界

| 目录 | 当前内容 | 生命周期 |
|---|---|---|
| `AI_Shop-backend/AI_Shop-agent/evaluation-evidence/current/` | 唯一 current：v9 final（Search 50、RAG 50、Agent 25） | 只读；普通质量主分母 |
| `AI_Shop-backend/AI_Shop-agent/evaluation/.runs/` | v9 development/regression/final 三个可见运行 | 只保留主线运行，旧运行已清理 |
| `evaluation-evidence/archive/` | v2 通过 final、v3-v8 失败 final | 只读；历史审计，不参与 current 分母 |
| `evaluation-evidence/benchmarks/` | 客服 HUMAN_VERIFIED/HTTP、Search paired replay、DB batch/N+1、Agent repeated、fault matrix | 只读辅助证据；不把门禁当质量总分 |
| `AI_Shop-backend/AI_Shop-agent/evaluation/.holdouts/` | final holdout（ignored）及来源指纹 | 不入 Git；只用于一次性 final 追溯 |

只删除已被 v9 主线替代且没有独立审计价值的旧 benchmark、重复 sealed 文件和旧 `.runs`；历史 final、客服 pending/provisional 包保留，
因为它们承担不可变追溯职责。`docs/evidence-manifest.json` 是 current/archive/辅助证据的唯一机器索引，不能通过删除路径来掩盖失败结果。

## 校验与重跑

Python 评测统一使用 Conda `shop` 环境，并在 Agent 目录执行：

```bash
conda activate shop
cd AI_Shop-backend/AI_Shop-agent
python -m evaluation.cli validate
python -m evaluation.cli customer-service-gold --dataset evaluation-evidence/benchmarks/customer-service/customer-service-human-v1-20260823/customer-service-human-v1.jsonl --mode rule
python -m evaluation.cli search-paired-replay --run-id <new-id> --output-dir <new-dir>
python -m evaluation.cli customer-service-http rebuild --source-report <raw-report> --dataset <human-gold> --output-dir <new-dir>
```

当前质量主张的最小复核顺序：先看 `docs/evaluation/AI质量评测与Badcase.md` 的点估计、分母、CI 和 badcase，再看 current 的原始
`cases.jsonl/bad-cases.jsonl/summary.json`；故障矩阵、DB benchmark、`pass^k` 和本地延迟属于独立诊断，不能替代 Search 或客服质量指标。

客服当前主数据已完成两名标注者盲标和 lead reviewer 冲突仲裁，冻结包位于
`AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-human-v1-20260823/`；
不得把规则基线、模型自评或 `pass^k` 当成线上成功率；当前 scorecard 同步引用 60 条 HUMAN_VERIFIED 客服 gold，但 `releaseGateEligible=false`，后续标签修订必须生成新版本。

同一 60 条的正式 HTTP Agent 路径证据位于 `customer-service-http-v1-20260823/`；当前完成执行/路由/转人工口径，答案语义质量待独立人工盲审。
新增 60 条 v2 数据及两份盲标表位于 `evaluation/datasets/customer_service/`，状态为 draft；仲裁前不与 v1 合并。Search 10 条难例回放位于
`evaluation-evidence/benchmarks/search/search-hard-negative-paired-v1-20260823/`，只用于已知难例回归和优化对照，不替代 v9 final。

人工复核工具已内置为 fail-closed 流程：`customer-service-review export` 生成无 expected/预测的双人盲标 sheet，`seal` 生成带哈希的不可变 sheet，`compare` 输出一致性与冲突 badcase，`merge` 只接受两份 sealed sheet，并要求所有冲突有仲裁记录。归档包同时保存 sealed 原件、仲裁文件、合并证据、报告、生命周期和 `SHA256SUMS`。
