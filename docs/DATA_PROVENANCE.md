# 数据与指标来源

> 最后更新：2026-07-29

这份文档只回答一个问题：**仓库里每个数字是怎么来的。**

接手（或被问到）本项目最容易踩的坑，是把合成数据、手工编写的样本、或者框架写好但从未
运行过的指标，当成真实基准来引用。下面按"可信程度"分层，写清每一项的产生方式和它**不能**
支撑什么结论。任何对外引用先在这里对一遍。

---

## 一、有真实测量值的指标

跑得出来、可复现的。

| 指标 | 数值 | 来源 | 边界 |
|------|------|------|------|
| 意图路由准确率 | **99/112 = 0.883929**（dev 0.911765 / test 0.840909） | `benchmarks/convo_eval_runner.py` 实测 | 112 条**手工编写**的 case，非真实流量；只评 `resolve_intent(allow_llm=False)` 这条确定性路径，**不是**线上端到端准确率 |
| 测试套件 | **291 passed, 1 skipped** | `python -m pytest tests/ -q` | skipped 那条需要真实 MySQL 8；大部分是纯函数与桩，不证明线上调用会成功 |
| 搜索相关性 Layer-1 | 通过 | `benchmarks/run_search_relevance.py`（taxonomy 断言） | 只验证分词/归一化逻辑，不碰 ES，也不涉及商品数据 |

基线的演进记录（90 → 96 → 99，题面 SHA-256 全程未变）和 13 条保留的已知失败，见
[`benchmarks/KNOWN_LIMITATIONS.md`](../AI_Shop-backend/AI_Shop-agent/benchmarks/KNOWN_LIMITATIONS.md)。

---

## 二、框架存在但当前没有数据的指标

代码写完了，产不出数字。**不要引用这些当成果。**

| 指标 | 为什么是空的 | 解除条件 |
|------|------------|---------|
| NDCG / MRR / Recall@K | 没有任何 `relevantProductIds` 标注，runner 直接返回 `{"skipped": true}` | `--emit-template` 出模板，人工标注约 200 对 query→商品 |
| CTR / 曝光点击比 | `redis_service.log_click` 只有函数定义，全仓库没有调用方，分子恒为 0 | 前端商品卡点击时上报 `(session_id, product_id)` |
| 曝光日志 | `log_impression` 已接在 `search_products` 里，但商品表为空 → 搜索无结果 → 写入路径实际没被走到 | 先装真实商品数据（见第三节） |
| 向量索引质量（kNN 召回率） | `aishop_vectorstore` 为空 | 装数据 + 跑 `scripts/vector_index.py` |

---

## 三、合成数据：程序生成，不是真实商品

| 数据 | 生成方式 | 可搜索 | 限制 |
|------|---------|--------|------|
| `data/generate_products.sql` 10k 条商品 | MySQL 存储过程随机生成，标题形如 `商品-101-000001` | ❌ 标题无任何品类词，BM25 和向量召回率都是 0 | **只能压测库表结构**，不能用来评搜索质量 |
| `02_catalog_seed.sql`（由 `scripts/generate_catalog.py` 生成） | LLM 生成，标题含 taxonomy 品类词 | ✅ | 合成但符合代码契约，可做功能验证和 NDCG 标注底稿；价格销量是随机值 |
| `total_sale` | Pareto 分布随机数 | — | 只为让分布"看起来长尾"，不代表真实销量 |
| `price` | 按 topic 价格段随机，覆盖 `_parse_budget` 能识别的区间 | — | 预算过滤功能可测，价格本身无市场意义 |
| `property_value` | 从 `temp_property_options` 候选池随机抽 | — | 颜色/尺码/品牌与商品标题不对应 |

**根因链**：干净库里 `sys_category` 为空 → `generate_products.sql` 产出 0 条 → ES 无索引 →
搜推全线空转。`data/01_category_seed.sql`（6 个一级 + 17 个二级类目 + 31 条属性）是修这条链的第一步。

---

## 四、手工编写的参考数据

不是从真实数据里统计出来的，所以覆盖完整性无法量化。

| 数据 | 位置 | 注意 |
|------|------|------|
| 112 条对话 case | `benchmarks/aishop_convo_v1.jsonl` | 手工写，覆盖 12 个意图；0.883929 是这批样本上的值 |
| `_BRAND_ALIASES`（14 个品牌） | `app/services/shopping_profile_service.py` | 手工维护，未从商品数据统计；`generate_catalog.py` 直接引用此表 |
| `search_taxonomy.yml` 12 个品类 | `app/config/` | 手工维护，未从搜索词日志统计。**注意 filler 词表是单个 regex alternation，顺序即优先级** |
| `_parse_budget` 价格段 | `app/services/shopping_profile_service.py` | 手工正则（百元内/千元内/三千内等口语说法），未从真实表述统计 |

---

## 五、Java 侧的真实行为数据

真实用户使用时会自然积累，非合成。

**Agent 已经消费的**（`java_internal_client.py`）：浏览历史 `browse_history_ids`、
购买历史 `purchase_history_product_ids`、共同购买 `co_purchase_product_ids` —— 已接入
`search_recommend_service` 的多路召回。

**还没消费的**：`user_product_favorite` · `user_search_keyword` · `product_cart` ·
`order_comment`（1–5 星）· `refund_request` · `agent_message_feedback`

接法一样：`UserAgentInternalController` 加内部接口 + `java_internal_client.py` 加方法，
不需要改 Java 业务逻辑。注意这些表在开发库里同样是空的——接口通不等于有数据。
