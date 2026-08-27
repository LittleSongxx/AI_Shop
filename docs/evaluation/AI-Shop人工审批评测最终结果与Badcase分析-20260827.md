# AI-Shop 人工审批评测最终结果与 Badcase 分析（2026-08-27）

## 结论

当前客服评测集和答案评审可以作为可信的人工决策证据使用：两位标注者及仲裁人的最终判断均由人工确认，AI 只辅助文字输出和落盘。证据等级统一记为 `HUMAN_APPROVED_AI_ASSISTED`；原始回传保持不变，派生证据只修正了误写的审批者身份字段，不声称“完全没有使用 AI”。

评测体系的总体设计正确，已经能分别衡量路由、风险、槽位、转人工、答案正确性、引用支持和安全性，并把运行成功、行为契约、人工一致率与模型质量分开。当前数据对开发回归和 badcase 定位有效，但它是开发者已见、已用于优化的 120 条客服集，不能证明 unseen 泛化、全项目 Search/RAG/Agent 总体质量、生产 SLO 或线上业务效果。

## 人工与数据闭环

| 项目 | 结果 | 解释 |
|---|---:|---|
| 标签政策复核 | 25 条 | A/B 完全一致 20 条，5 条人工仲裁 |
| 相对 v2 发生标签变化 | 19/25 | 这是审计定向样本，不可外推成全量 15.8% 标签错误率 |
| v2.1 successor | 120 条 | 新数据 SHA-256 `02a6dacc6a2aadb88c6dfb60bf7a74e2f083fcba0f9a6e82fef38c4dfa82caf3` |
| v43 答案评审 | 120 条 | A/B 案件级完全一致 117 条，3 条人工仲裁 |
| v54 修复后答案评审 | 120 条 | A/B 案件级完全一致 112 条，8 条人工仲裁 |
| v56 v3 知识回归答案评审 | 120 条 | A/B 案件级完全一致 118 条，2 条人工仲裁 |
| 评审证据等级 | `HUMAN_APPROVED_AI_ASSISTED` | 人工拥有最终决策权；AI 辅助表达和记录 |

19 条标签变化中，`slots` 涉及 13 条，intent/risk/handoff 相关字段各涉及 3 条。高变更比例说明此前发现的 taxonomy/槽位政策问题真实存在，也说明定向重审有效；它不代表未抽样的 95 条具有同样错误率。

## 当前可报告指标

### 冻结 v43 答案质量（修复前基线）

| 指标 | 结果 | Wilson 95% CI | 结论 |
|---|---:|---:|---|
| 答案正确率 | 107/120 = 89.17% | 82.34%–93.56% | 仍有 13 条答案错误 |
| 可计分引用支持率 | 66/70 = 94.29% | 86.21%–97.76% | 4 条无可见证据支持 |
| 转人工适当率 | 118/120 = 98.33% | 94.13%–99.54% | 2 条应转未转 |
| Unsafe answer rate | 1/120 = 0.83% | 0.15%–4.57% | 不能称零风险 |
| 联合质量通过率 | 105/120 = 87.50% | 80.40%–92.28% | 15 条至少一项失败 |

这组数字直接衡量冻结答案在当前 120 条场景上的质量，是真正的人工语义结果；但不是未来模型输出或线上会话的无偏估计。

### 冻结 v54 修复后答案质量

| 指标 | 结果 | Wilson 95% CI | 结论 |
|---|---:|---:|---|
| 答案正确率 | 116/120 = 96.67% | 91.74%–98.70% | 4 条答案错误 |
| 可计分引用支持率 | 63/67 = 94.03% | 85.63%–97.65% | 4 条引用不支持 |
| 转人工适当率 | 120/120 = 100% | 96.90%–100% | 当前集合无失败，不代表绝对不会失败 |
| Unsafe answer rate | 1/120 = 0.83% | 0.15%–4.57% | `cs-candidate-v2-110` 仍有现实权益风险 |
| 联合质量通过率 | 113/120 = 94.17% | 88.45%–97.15% | 7 条至少一项失败 |

同一已见 120 条上，v43→v54 联合通过从 `105` 增至 `113`：13 条 fail→pass、5 条 pass→fail、2 条仍 fail。联合指标的 exact McNemar 双侧 `p=0.096252`；这支持“已知集净改善”，但不足以声称已证明 unseen 泛化。

### 冻结 v56 最终答案质量

| 指标 | 结果 | Wilson 95% CI | 结论 |
|---|---:|---:|---|
| 答案正确率 | 120/120 = 100% | 96.90%–100% | 当前集合无失败，不代表未来绝对正确 |
| 可计分引用支持率 | 67/67 = 100% | 94.58%–100% | 当前 eligible 引用无失败 |
| 转人工适当率 | 120/120 = 100% | 96.90%–100% | 当前集合无失败，不代表绝对不会错误接管 |
| Unsafe answer rate | 0/120 = 0% | 0%–3.10% | 点估计为 0，区间上界仍非 0 |
| 联合质量通过率 | 120/120 = 100% | 96.90%–100% | 最终人工 badcase 0 |

A/B 案件级完全一致 `118/120`，`cs-gold-v1-026`、`cs-candidate-v2-096` 由第三位人工仲裁。两条最终均通过，冻结字段改动为 0。v54→v56 联合通过为 113→120，7 条 fail→pass、0 条 pass→fail；探索性 exact McNemar 双侧 `p=0.015625`。由于这些正是已知 badcase 的定向修复且样本被反复暴露，该数字只能证明同集回归，不能证明 unseen 泛化。

### v2.1 标签下的冻结路由重算

| 指标 | 结果 | 解释 |
|---|---:|---|
| Intent Macro-F1 | 1.0000 | 当前可见开发集已被多轮优化，适合作回归，不作泛化证明 |
| 高风险 intent Recall | 13/13 = 1.0000 | 样本仍小，95% CI 下界约 77.19% |
| Handoff Recall | 29/29 = 1.0000 | 95% CI 下界约 88.30% |
| Critical handoff miss | 0/9 | 95% CI 上界约 29.91%，不能写成绝对不会漏接管 |
| Slot span F1 | 0.4826 | 明显未达参考目标 0.95 |
| Slot exact match | 29/68 = 0.4265 | 明显未达参考目标 0.90 |

槽位 badcase 共 44 条：`SLOT_EXTRACTION_GAP` 32 条、`SLOT_NORMALIZATION_GAP` 6 条、`GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED` 6 条。数据集因此不是“无效”，反而正确暴露了当前生产预测的槽位抽取和 schema 映射缺口；在这些问题修复前，不能宣称客服结构化理解整体合格。

## v43 的 15 条答案 badcase（修复前）

| 根因 | case | 优先级 |
|---|---|---|
| 待付款订单被写成“实付金额”，并错误暗示退款 | `cs-gold-v1-012`、`cs-candidate-v2-067` | P0 交易事实 |
| 只建议找人工但未真正转接；高金额订单消失未升级 | `cs-candidate-v2-061`、`cs-candidate-v2-111` | P0 权益与接管 |
| 当前日期、通用售后、支付重试、商品技术对比、优惠券原因回答不完整 | `070`、`078`、`090`、`091`、`092`、`101` | P1 答案覆盖 |
| 推荐未满足预算/品类/小户型/静音硬约束 | `099`、`114` | P1 检索约束 |
| 编入无来源的详情页或 24–48 小时出库规则 | `103`、`104` | P1 Grounding |
| 颜色不符被错误描述为商品破损 | `079` | P1 售后分类 |

`cs-candidate-v2-111` 是当前最高风险 badcase：它同时失败于答案正确、转人工适当和 unsafe，必须在下一次全链路回放前修复并加入确定性行为契约。

## v54 的 7 条历史 badcase（已在 v56 关闭）

| 根因 | case | 优先级 |
|---|---|---|
| 订单未匹配缺少澄清和“不等于不存在”边界，可能影响用户权益 | `cs-candidate-v2-110` | P0 答案/unsafe |
| 退款条件问题被内部幂等细节或当前资格结论带偏 | `cs-gold-v1-036`、`cs-candidate-v2-116` | P1 答案覆盖 |
| 实质性支付重试/技术事实无可见来源 | `cs-gold-v1-048`、`cs-candidate-v2-090`、`cs-candidate-v2-092` | P1 Grounding |
| 用户明确只反馈且不要转人工，回答仍套用转人工模板 | `cs-candidate-v2-075` | P1 指令遵循 |

以上 7 条在 v55 定向回放和 v56 全量复评中均已通过；v56 最终人工 badcase 为 0。

## 评测有效性判断

| 要回答的问题 | 当前证据是否有效 |
|---|---|
| 已知客服场景的路由和接管是否回归 | 有效 |
| 当前冻结 v43/v54/v56 答案在这 120 条上是否正确、有依据、安全 | 有效 |
| 槽位抽取是否达到目标 | 有效，结论是不合格 |
| 对新表达、新商品和新订单状态是否泛化 | 无法证明 |
| Search/RAG/Agent 全项目总体性能 | 无法由本客服集代表 |
| 生产 SLO、CSAT/FCR、CTR/CVR/GMV | 无法证明 |

因此，当前数据适合做开发质量基线、回归门禁和修复优先级排序；不适合包装成“生产级总体准确率”或独立 final holdout。

## 后续执行顺序

1. 保持 v54/v56 immutable 证据不变；以后每次模型、知识库或路由发生实质变化都生成新 observation 和新人工包。
2. 对 44 条槽位 badcase 分开处理 extraction、normalization 和 gold-schema mapping；修复后在同一 v2.1 上重算，只声明 paired regression。
3. 由独立保管者完成全 60 条来源复核并新建仓库外 unseen final，之后才讨论 release gate 和泛化质量。
4. 新增时间漂移、真实商品变化、长对话和对抗安全样本，避免 120 条固定集饱和后失去区分度。

## 权威文件

- v43 最终答案证据：`evaluation-evidence/benchmarks/customer-service/customer-service-http-v43-answer-review-human-approved-ai-assisted-20260827/`
- v54 最终答案证据：`evaluation-evidence/benchmarks/customer-service/customer-service-http-v54-badcase-fixes-answer-review-human-approved-ai-assisted-20260827/`
- v54 C 原始回传封存：`evaluation-evidence/intake-archive/customer-service-v54-answer-review-adjudication-return-human-approved-ai-assisted-20260827/`
- v56 最终答案证据：`evaluation-evidence/benchmarks/customer-service/customer-service-http-v56-v3-knowledge-answer-review-human-approved-ai-assisted-20260827/`
- v56 A/B 与 C 原始回传封存：`evaluation-evidence/intake-archive/customer-service-v56-answer-review-round1-returns-human-approved-ai-assisted-20260827/`、`evaluation-evidence/intake-archive/customer-service-v56-answer-review-adjudication-return-human-approved-ai-assisted-20260827/`
- v2.1 标签与路由证据：`evaluation-evidence/benchmarks/customer-service/customer-service-human-v2.1-label-policy-human-approved-ai-assisted-20260827/`
- v2.1 successor：`evaluation/datasets/customer_service/adjudicated/customer-service-human-v2.1-human-approved-ai-assisted.jsonl`
- 原始仲裁回传归档：`evaluation-evidence/intake-archive/human-review-adjudication-returns-20260827/`
- 人工审批/AI 辅助说明：`evaluation-evidence/intake-archive/human-review-human-approval-ai-assistance-provenance-20260827/`

以上路径均相对于 `AI_Shop-backend/AI_Shop-agent/`，跨包哈希由 `docs/evidence-manifest.json` 校验。
