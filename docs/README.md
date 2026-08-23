# AI_Shop 文档与证据入口

文档按内容归档，不按日期复制新文件。后续同类变化在同一份稳定文档的“增量记录”中追加；日期只用于说明开发阶段。

| 目录/文件 | 内容 | 证据等级 |
|---|---|---|
| [project/AI_Shop主线与开发记录](project/AI_Shop主线与开发记录.md) | AI 主线、方法判断、关键改造、阶段性取舍和后续路线 | 源码与运行证据汇总 |
| [evaluation/AI质量评测与Badcase](evaluation/AI质量评测与Badcase.md) | Search/RAG/Agent 质量指标、门禁、badcase、性能和限制 | current/archive 证据包投影 |
| [evaluation/AI质量评测与Badcase.json](evaluation/AI质量评测与Badcase.json) | Search 指标及逐指标 badcase 的机器可读结果 | immutable scorecard 投影 |
| [evaluation/customer-service/客服金标评测](evaluation/customer-service/客服金标评测.md) | 60 条双人盲标+第三人仲裁的 intent、风险、slot、handoff 质量证据 | `HUMAN_VERIFIED` 离线证据，仍不进入 release gate |
| [evaluation/customer-service/客服金标评测.json](evaluation/customer-service/客服金标评测.json) | 客服评测逐 case、canonical slot 诊断和 badcase | 可复核机器证据 |
| [evidence-manifest.json](evidence-manifest.json) | current、archive、visible run、哈希和生命周期机器索引 | 机器校验入口 |

项目根目录的 [Java 后端面试报告](../AI应用开发_Java后端_真实面试题与备考报告_20260821.md) 是独立的求职研究材料，不与项目开发日志重复合并。

## 校验与重跑

Python 评测统一使用 Conda `shop` 环境，并在 Agent 目录执行：

```bash
conda activate shop
cd AI_Shop-backend/AI_Shop-agent
python -m evaluation.cli validate
python -m evaluation.cli customer-service-gold --mode rule
```

客服当前主数据已完成两名标注者盲标和 lead reviewer 冲突仲裁，冻结包位于
`AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-human-v1-20260823/`；
不得把规则基线、模型自评或 `pass^k` 当成线上成功率，后续标签修订必须生成新版本。

人工复核工具已内置为 fail-closed 流程：`customer-service-review export` 生成无 expected/预测的双人盲标 sheet，`seal` 生成带哈希的不可变 sheet，`compare` 输出一致性与冲突 badcase，`merge` 只接受两份 sealed sheet，并要求所有冲突有仲裁记录。归档包同时保存 sealed 原件、仲裁文件、合并证据、报告、生命周期和 `SHA256SUMS`。
