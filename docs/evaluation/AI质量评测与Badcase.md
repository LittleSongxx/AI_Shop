# AI_Shop 质量评测与 Badcase

> 当前证据：`release-20260822-ai-quality-v9` / `final-20260822-ai-quality-v9`
> 统计环境：Conda `shop`；所有延迟为本地完整链路观测，不是生产 SLO。

## 先看口径

质量指标回答“结果好不好”，门禁回答“是否允许发布”。门禁必须 100%，不作为优展示；Search 排名、客服理解和 RAG 证据质量才是
面试中应重点陈述的数值。每个质量指标都保留分母、95% 区间和 badcase，不能因为 runtime `PASS` 就认为没有质量坏例。

## 当前主质量结果

| 域/指标 | 点估计 | 分子/分母 | 95% CI | 质量 badcase |
|---|---:|---:|---|---:|
| Search Recall@10（macro/query） | 0.962121 | query macro | bootstrap | 3 query / 4 qrel |
| Search Recall@10（micro/qrel） | 0.928571 | 52/56 | bootstrap | 4 漏召回商品 |
| Search MRR@10 | 0.937500 | 44 query | bootstrap | 5 query |
| Search NDCG@10 | 0.920521 | 44 query | bootstrap | 10 query |
| RAG grounded faithfulness | 1.000000 | 50/50 | Wilson | 规则 lexical 下界，无语义人工真值 |
| RAG citation support | 1.000000 | 50/50 | Wilson | 无；semantic judge 只作 shadow |
| AI 客服 intent/slot/handoff | 见下节 | 独立 60 条 draft gold | bootstrap/Wilson | 当前 0；优化前 8 条保留 |

### Search slice（不加权掩盖失败）

| slice | case/judged | Recall@10 | MRR@10 | NDCG@10 | badcase |
|---|---:|---:|---:|---:|---|
| exact-model-number-brand | 10/10 | 1.0000 | 1.0000 | 1.0000 | 无 |
| chinese-synonym-oral | 10/10 | 1.0000 | 0.9500 | 0.9693 | `search-fin-v9-11-office` |
| budget-structured | 8/8 | 0.9167 | 0.9062 | 0.8880 | `search-fin-v9-23-snack-100`, `search-fin-v9-28-lip-100` |
| negative-exclusion | 8/8 | 0.9375 | 0.9375 | 0.8710 | `search-fin-v9-33-coat-no-outdoor`, `search-fin-v9-34-snack-no-wangwang` |
| no-result-conflict | 6/0 | 不可得 | 不可得 | 不可得 | 无 qrel，不能把门禁当质量分数 |
| fallback-partial-provider | 4/4 | 1.0000 | 0.8750 | 0.8712 | `search-fin-v9-43-partial-headset`, `search-fin-v9-44-partial-office` |
| category-brand-comparison | 4/4 | 0.8750 | 0.8750 | 0.8130 | `search-fin-v9-47-compare-xm`, `search-fin-v9-49-compare-lip`, `search-fin-v9-50-compare-home` |

已确认的 Search hard negative：多商品/多品牌 conjunction、否定约束候选不足、比较对象过早收窄。本阶段只记录，不修复、不重刷 final。

## AI 客服四项关键证据

完整逐 case 结果见 [客服金标评测](customer-service/客服金标评测.md)，机器证据见 [客服 JSON](customer-service/客服金标评测.json)。当前是
规则预路由基线，不是完整 HTTP Agent，也不是人工真值。数据集已从 40 条扩展到 60 条，新增难样本集中在商品咨询/搜索边界、支付风险、隐私、否定、少件和槽位格式。

| 指标 | 当前值 | 分子/分母 | 95% CI | badcase |
|---|---:|---:|---|---:|
| Intent Macro-F1 | 1.000000 | 20 observed intents | [0.700000, 0.950000] | 当前无；历史 001/003/011/026/031/032 |
| 高风险 intent Recall | 1.000000 | 10/10 | [0.722467, 1.000000] | 当前无；历史 022/026 |
| Slot entity/span F1 | 1.000000 | 344/344 span chars | [1.000000, 1.000000] | 当前无；历史 001/002/003 |
| 请求级 Slot Exact Match | 1.000000 | 34/34 non-empty-slot cases | [0.898485, 1.000000] | 当前无；历史 001/002/003 |
| Handoff Recall | 1.000000 | 14/14 | [0.784689, 1.000000] | 当前无；历史 026 |
| 严重漏转人工率 | 0.000000（越低越好） | 0/10 critical | [0.000000, 0.277533] | 当前无；历史 026 |

关键 badcase 和根因素材：

- `cs-gold-v1-003`：商品规格咨询被生产规则判为 `PRODUCT_SEARCH`，且没有 productName entity。
- `cs-gold-v1-011`：泛化退款到账政策落到 `CHAT`，需要区分政策问法与个人退款进度。
- `cs-gold-v1-022`：投诉/报警已正确转人工但 risk 仅为 `MEDIUM`，高风险识别漏标。
- `cs-gold-v1-026`：邮箱历史请求落到 `CHAT + HANDOFF_SUGGESTED`，严重隐私请求未即时转人工。
- `cs-gold-v1-032`：短句“收到商品是坏的”落到 `CHAT`，破损售后意图漏识别。
- `cs-gold-v1-001/002`：订单/金额能抽取，但商品名没有进入结构化 entities，slot EM 失败。

标签尚未独立人工复核，所有指标标记 `PROVISIONAL_NOT_HUMAN_GOLD`、`releaseGateEligible=false`；点估计 1.0 只说明这 60 条规则诊断样本全部命中，不能外推为人工准确率或生产稳定性。复核后必须重新冻结并运行，不能覆盖本 provisional 结果。

最近一次修复了首轮低风险“支付方式有哪些”误建议转人工的策略边界；客服专项回归 `102 passed`，全量 Python `1244 passed, 7 skipped`（跳过项均要求真实 MySQL 8）。

## RAG 与 Agent 边界

- RAG 保留 lexical claim、事实 ID、引用支持和 no-answer 作为安全下界；semantic shadow judge 记录 prompt/model/provider/claim/证据和
  disagreement，但未完成校准前不进入门禁，不称人工准确率。
- Agent `pass^5`/`pass^8`、tool routing、终态、state diff、幂等和重复副作用是可靠性门禁，不是客服 intent F1，也不是开放世界成功率。
- 当前 Agent final 25 条、200 trials 的 `pass^8=1.0` 只说明冻结任务集中的声明契约满足；本报告的 60 条客服 draft gold 才开始测理解质量。

## 必须 100% 的发布契约

Search hard constraint/no-result/provider completeness、RAG invalid citation/严重安全/runtime error、Agent terminal state/state diff/重复副作用/
runtime safety error 必须全通过；任何失败阻断发布。不要用这些通过率替代主质量指标。

## Usage、成本和 DB 证据

Provider 未返回 usage 记 `MISSING_USAGE`；无可信单价时 `costCny=null`，不写成零。v9 final 的部分 deterministic path 缺 usage，RAG 有 token
但价格未知，因此没有费用硬门禁。隔离 MySQL benchmark 在候选 `1/10/50/100` 比较 batch 与 N+1：100 候选时 batch 1 次 round trip、N+1 100 次，
错误率 0、rollback probe 通过；这是本地描述性 benchmark，不是线上容量/SLO。

## 证据与复现

```bash
conda activate shop
cd AI_Shop-backend/AI_Shop-agent
python -m evaluation.cli validate
python -m evaluation.cli slices --split development
python -m evaluation.cli customer-service-gold --mode rule
```

current 只指向 v9 final；v2 是历史通过 archive，v3-v8 是 immutable failed archive；旧运行不参与当前分母。机器索引、哈希和生命周期见
[evidence-manifest.json](../evidence-manifest.json)。

## 当前不做的外推

没有真实曝光/点击/购买、人工盲评、生产并发、支付合规或长期线上实验，因此不能声称 CTR/CVR/GMV、工业级个性化推荐、生产 SLO、人工语义
准确率或开放世界客服成功率。下一步优先级是独立人工复核 60 条标签、补充一批真实脱敏对话并冻结版本；Search hard negative 延后做 paired replay。
