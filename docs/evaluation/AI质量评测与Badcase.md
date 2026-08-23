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
| AI 客服 intent/slot/handoff | 见下节 | 独立 60 条双人盲标+仲裁 gold | bootstrap/Wilson | 3 intent、15 full-slot badcase |

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

完整逐 case 结果见 [客服金标评测](customer-service/客服金标评测.md)，机器证据见 [客服 JSON](customer-service/客服金标评测.json)。这是
生产 `resolve_intent(..., allow_llm=False)` 规则预路由基线，不是完整 HTTP Agent；标签已完成双人盲标和 lead reviewer 仲裁，状态为
`HUMAN_VERIFIED`，但仍是离线证据且不自动进入 release gate。数据集 60 条，覆盖商品咨询/搜索边界、支付风险、隐私、否定、少件和槽位格式。

| 指标 | 当前值 | 分子/分母 | 95% CI | badcase |
|---|---:|---:|---|---:|
| Intent Macro-F1 | 0.955299 | 19.105978/20 intents | [0.642094, 0.926190] | `011,044,057`：政策/比较边界 |
| 高风险 intent Recall | 1.000000 | 10/10 | [0.722467, 1.000000] | 无 |
| Slot entity/span F1（完整人工 schema） | 0.907652 | 344/414 chars | [0.884563, 0.961881] | 15；扩展槽位/归一化/漏抽 |
| 请求级 Slot Exact Match（完整人工 schema） | 0.558824 | 19/34 non-empty-slot cases | [0.394539, 0.711165] | 同上 |
| Handoff Recall | 1.000000 | 14/14 | [0.784689, 1.000000] | 无 |
| 严重漏转人工率 | 0.000000（越低越好） | 0/6 critical | [0.000000, 0.390334] | 无 |

关键 badcase 和根因素材：

- `cs-gold-v1-011` / `057`：退款政策/到账时效分别被路由到 `REFUND_STATUS`、`CHAT`，taxonomy 与政策/状态边界不一致。
- `cs-gold-v1-044`：比较型问题被判为 `PRODUCT_SEARCH`，应进入商品咨询/比较路径。
- `cs-gold-v1-001/002/003/029/033/034/041/042/043/045/059`：人工仲裁保留 `brand/budget/feature/兼容型号` 等扩展槽位，生产 extractor 尚未映射；不应全部归因于模型漏抽。
- `cs-gold-v1-009/020/058`：金额币种/单位归一化差异；`cs-gold-v1-055`：canonical `productName/quantity` 仍未抽出。

生产 canonical slot 投影（`orderId/orderItemId/productId/productName/amount`）为 Span F1 `0.992785`、EM `0.882353`，作为 schema 对齐诊断，不替换完整人工 schema 主指标。所有指标均保留逐 case 输入、gold、prediction 和根因；当前不能外推为线上客服成功率、CSAT 或 FCR。

最近一次修复了首轮低风险“支付方式有哪些”误建议转人工的策略边界；人工闭环和 canonical 诊断专项测试已通过，完整测试结果以 CI 输出为准。

## RAG 与 Agent 边界

- RAG 保留 lexical claim、事实 ID、引用支持和 no-answer 作为安全下界；semantic shadow judge 记录 prompt/model/provider/claim/证据和
  disagreement，但未完成校准前不进入门禁，不称人工准确率。
- Agent `pass^5`/`pass^8`、tool routing、终态、state diff、幂等和重复副作用是可靠性门禁，不是客服 intent F1，也不是开放世界成功率。
- 当前 Agent final 25 条、200 trials 的 `pass^8=1.0` 只说明冻结任务集中的声明契约满足；本报告的 60 条客服 HUMAN_VERIFIED gold 才测理解质量。

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
# 客服人工金标已完成；新版本仍必须按同一 fail-closed 流程 seal/compare/merge
python -m evaluation.cli customer-service-review export --annotator reviewer-a --output /tmp/reviewer-a.open.jsonl
```

current 只指向 v9 final；v2 是历史通过 archive，v3-v8 是 immutable failed archive；旧运行不参与当前分母。机器索引、哈希和生命周期见
[evidence-manifest.json](../evidence-manifest.json)。

## 当前不做的外推

没有真实曝光/点击/购买、客服满意度盲评、生产并发、支付合规或长期线上实验，因此不能声称 CTR/CVR/GMV、工业级个性化推荐、生产 SLO、CSAT/FCR
或开放世界客服成功率。下一步优先级是将 `011/044/057` 路由边界和 `055/058` canonical 槽位/金额归一化加入回归切片，再对扩展槽位决定是否纳入生产 extractor；Search hard negative 延后做 paired replay。
