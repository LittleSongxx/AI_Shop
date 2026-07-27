# AI_Shop 搜推现状与后续路线

> 最后更新：2026-07-27  
> 当前分支：`feat/rec-upgrade`  
> 适用读者：接手本项目的 AI 或工程师

---

## 一、项目定位（一句话）

Java Spring Boot 微服务（12 个子服务，一域一库）+ Python LangGraph ReAct Agent，搜推能力处于 **Phase-1 检索系统**，评测基建达到 Phase-2 起点。链路完整可用，前提是先把真实商品数据装进去（见第四节）。

---

## 二、已完成的工作

### 2.1 意图路由修复（基线已锁定）

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 整体通过率 | 90/112 = 0.8036 | **96/112 = 0.8571** |
| 已知失败 | 22 条 | **16 条** |

修复内容：`howto→CHAT` 分支优先级调整、`CANCEL_ORDER` 工具路由、`QUERY_COUPON` 复合句保护、Prompt 意图枚举对齐。  
基线文件：`AI_Shop-backend/AI_Shop-agent/benchmarks/aishop_convo_v1.lock.json`  
已知失败详情：`AI_Shop-backend/AI_Shop-agent/benchmarks/KNOWN_LIMITATIONS.md`

### 2.2 搜推六项升级（N1–N6，均已实现）

| 项 | 状态 | 关键文件 | 实际可用条件 |
|----|------|---------|-------------|
| N1 曝光埋点 | ✅ 服务端完成 | `app/services/product_service.py` `log_impression` | **click 侧无调用方**，CTR 不可算；需前端/Java 打 click 事件 |
| N2 LLM 商品扩写 | ✅ 脚本完成 | `scripts/enrich_products.py` | 需先有真实商品标题才能扩写，占位标题会被跳过 |
| N3 放宽澄清阈值 | ✅ 已改 | `app/services/shopping_profile_service.py` | 现在：≤40字且有品类但无场景/预算时触发 |
| N4 i2i 相似召回 | ✅ 已实现 | `app/rag/retriever.py` | 向量质量依赖真实商品数据 |
| N5 搜索相关性评测 | ✅ 框架完成 | `benchmarks/run_search_relevance.py` | Layer-1（taxonomy断言）可运行；Layer-2（NDCG/MRR）需人工标注 ~200 对 query→商品 |
| N6 taxonomy 补充 | ✅ 已完成 | `app/config/search_taxonomy.yml` | 已补无线耳机/空气炸锅等品类 |

### 2.3 数据基础设施（本次新增，核心）

| 文件 | 作用 | 状态 |
|------|------|------|
| `AI_Shop-backend/data/01_category_seed.sql` | 6 个一级 + 17 个二级类目 + 31 条属性 + 17 条品牌属性 | ✅ 已写，**未入库** |
| `AI_Shop-backend/data/generate_products.sql` | 生成 10k 占位商品（标题为`商品-101-000001`，不可搜索） | ✅ stock 列 bug 已修，可正常执行 |
| `AI_Shop-backend/AI_Shop-agent/scripts/generate_catalog.py` | LLM 生成真实感商品目录，输出 `02_catalog_seed.sql` | ✅ 已写，**待执行** |

**根因说明**：干净数据库上 `sys_category` 为空 → `generate_products.sql` 产出 0 条商品 → ES 无索引 → 搜推全线空转。`01_category_seed.sql` 是修复根因的第一步。

### 2.4 测试状态

```
224 passed, 1 skipped（skipped = 需要真实 MySQL 8，离线无法运行）
conda 环境：shop（Python 3.12.13）
运行命令：cd AI_Shop-backend/AI_Shop-agent && python -m pytest tests/ -q
```

---

## 三、数据与指标来源说明

接手本项目时最容易踩的坑：把合成数据或从未运行过的指标当作真实基准。下表做明确区分。

### 3.1 已有真实值的指标

| 指标 | 数值 | 来源 | 备注 |
|------|------|------|------|
| 意图路由准确率 | **96/112 = 0.8571** | 实际运行 `convo_eval_runner.py` | 在 112 条**手工编写**的对话测试用例上测得，非真实用户流量 |
| 测试套件 | **224 passed, 1 skipped** | 实际运行 `pytest tests/` | 覆盖 taxonomy 断言、N5 Layer-1、埋点逻辑等纯函数；skipped=需要真实 MySQL |
| N5 Layer-1（taxonomy 断言）| 运行通过 | 纯函数，无外部依赖 | 只验证分词/归一化逻辑正确性，不涉及 ES 或商品 |

### 3.2 当前值为空——框架存在但从未产出数据

| 指标 | 为什么是空的 | 解除条件 |
|------|------------|---------|
| **NDCG / MRR / Recall@K**（N5 Layer-2）| 无任何 `relevantProductIds` 标注；运行后返回 `{"skipped": True, "reason": "No case carries relevantProductIds"}` | 先跑 `--emit-template` 生成模板，人工标注 ~200 对 query→商品 |
| **CTR / 曝光点击比** | `log_click` 只有函数定义，无任何调用方；分子永远为 0 | 前端商品卡片点击时上报 `(session_id, product_id)` |
| **曝光日志** | `log_impression` 代码已写，但商品表为空 → 搜索永远无结果 → 函数从未被实际执行到写入路径 | 先装数据（第四节），有搜索结果后自然写入 |
| **向量索引质量**（kNN 召回率）| `aishop_vectorstore` 为空或仅含 Spring AI 默认数据 | 装数据 + 运行 `scripts/vector_index.py` |

### 3.3 合成数据——由程序生成，非真实用户/商品

| 数据 | 生成方式 | 可搜索性 | 用途限制 |
|------|---------|---------|---------|
| **`generate_products.sql` 10k 条商品** | MySQL 存储过程随机生成，标题格式为 `商品-101-000001` | ❌ 完全不可搜索（标题无任何品类词，BM25/向量召回率为 0）| 只能用于压测数据库结构，不能用于评测搜索质量 |
| **`02_catalog_seed.sql`（待生成）** | 由 `generate_catalog.py` 调 LLM 生成，标题含真实品类词 | ✅ 可搜索（每条标题含 taxonomy term）| 合成但符合四个代码契约，可用于功能验证和 NDCG 标注；销量/价格为随机值，不反映真实市场 |
| **`total_sale` 字段** | Pareto 分布随机数（`generate_catalog.py`）| — | 不代表真实销量，仅使数据分布"看起来长尾" |
| **`price` 字段** | 按 topic 价格段随机，覆盖 `_parse_budget` 能识别的区间 | — | 合成价格；预算过滤功能可测，但价格本身无市场意义 |
| **`property_value` 属性值** | 从 `temp_property_options` 候选池随机抽取（`generate_products.sql`）| — | 颜色/尺码/品牌等值随机，不与商品标题对应 |

### 3.4 手工编写的参考数据（非从真实数据中提取）

| 数据 | 来源 | 注意 |
|------|------|------|
| **112 条对话测试用例** | 手工编写，覆盖12个意图类别 | 非真实用户对话日志；准确率 0.8571 是在这批样本上的，不代表真实流量表现 |
| **`_BRAND_ALIASES`（14个品牌）** | 手工维护在 `shopping_profile_service.py` | 未从商品数据中统计得出；`generate_catalog.py` 的品牌列表直接引用此处 |
| **`search_taxonomy.yml` 12个品类** | 手工维护 | 未从用户搜索词日志中统计得出；品类覆盖完整性无法量化 |
| **`_parse_budget` 价格段** | 手工编写正则（百元内/千元内/三千内等口语词）| 未从用户真实表述中统计；`generate_catalog.py` 的价格分布按此校准，使价格段过滤功能可测 |

### 3.5 Java 侧已存在但 Agent 未消费的真实行为数据

以下数据在真实用户使用时会积累（非合成），但当前 Agent 侧读取不到：

`user_browse_history` · `user_product_favorite` · `user_search_keyword` · `product_cart` · `order_item`（购买记录）· `order_comment`（1-5 星评分）· `refund_request` · `agent_message_feedback`

扩展方式：`UserAgentInternalController` + `java_internal_client.py` 各加接口，无需改 Java 业务逻辑。

---

## 四、核心文件地图

### Python Agent（`AI_Shop-backend/AI_Shop-agent/`）

| 文件 | 职责 |
|------|------|
| `app/config/search_taxonomy.yml` | 12 个品类 canonical/aliases/terms，filler 词表，标点表。**ordering is semantics**（filler 是单个 regex alternation，顺序就是优先级）|
| `app/rag/retriever.py` | ES BM25 (`aishop-index`) + kNN (`aishop_vectorstore`, 1024-dim, threshold=0.4, `text-embedding-v4`) 双路召回 |
| `app/rag/rrf.py` | RRF 融合（k=60，固定公式，非学习权重）|
| `app/services/product_service.py` | `search_products`：召回→重排（`gte-rerank-v2`）→ `log_impression`；`load_similar_products`：i2i kNN |
| `app/services/shopping_profile_service.py` | Rule/regex 提取预算/品牌/场景/特征；`should_clarify` 阈值（已改为≤40字+有品类无场景/预算）；`_BRAND_ALIASES`（14 个品牌的规范名+别名）|
| `app/domain/intent/classifier.py` | 意图路由；`howto→CHAT` 分支现在在 INVOICE/ADDRESS_CHANGE 之前；`_TOOL_INTENTS` 含 `CANCEL_ORDER` |
| `prompts/user_intent.txt` | LLM fallback 层的意图提示词（12种意图，含 CONFIRM_RECEIPT/CANCEL_ORDER 区分示例）|
| `benchmarks/run_search_relevance.py` | 双层评测：Layer-1 taxonomy 断言（纯函数，无网络），Layer-2 graded NDCG/MRR/Recall@K（需 ES + 标注数据）|
| `benchmarks/aishop_convo_v1.lock.json` | 冻结基线 96/112，knownFailures 16 条，`--bootstrap-lock` 重生成 |
| `scripts/enrich_products.py` | LLM 批量扩写商品标题，写入 `enriched-product-<id>` 命名空间，有 Redis 缓存 + resume 状态 |
| `scripts/generate_catalog.py` | **新增**：LLM 生成真实商品目录 SQL，满足 taxonomy terms / `_BRAND_ALIASES` / 品牌属性 / 价格段四个契约 |
| `scripts/vector_index.py` | 重建向量索引 |

### 数据文件（`AI_Shop-backend/data/`）

| 文件 | 说明 |
|------|------|
| `01_category_seed.sql` | 类目 + 规格属性种子，**必须最先执行** |
| `generate_products.sql` | 10k 占位商品（标题不可搜索，但 stock 列 bug 已修）|
| `02_catalog_seed.sql` | 由 `generate_catalog.py` 生成，**尚不存在**，需执行脚本才会产生 |

### Java 微服务（侵入性最小原则）

`java_internal_client.py` 当前只暴露 `latest_browse_product_id`。以下行为数据**已在 Java 侧落库但 Agent 未消费**：`user_browse_history`、`user_product_favorite`、`user_search_keyword`、`product_cart`、`order_item`、`order_comment`（1-5星）。扩展方法是在 `UserAgentInternalController` + `java_internal_client.py` 各加接口，无需改 Java 业务逻辑。

---

## 四、启动数据（第一件事）

系统当前在干净数据库上产出 0 条商品。按顺序执行：

```bash
# 1. 类目 + 属性种子（修复根因）
mysql -uroot -p aishop_product < AI_Shop-backend/data/01_category_seed.sql

# 2. 生成真实感商品目录（需要 LLM API，约 $5-20，~1小时）
cd AI_Shop-backend/AI_Shop-agent
python scripts/generate_catalog.py --per-topic 40    # 约 480 条，先跑小批
# python scripts/generate_catalog.py --dry-run --per-topic 2  # 预览不花钱

# 3. 写入数据库
mysql -uroot -p aishop_product < AI_Shop-backend/data/02_catalog_seed.sql

# 4. 重建向量索引
python scripts/vector_index.py

# 5. 可选：LLM 扩写（进一步改善向量召回质量）
python scripts/enrich_products.py --limit 50 --dry-run  # 先预览
python scripts/enrich_products.py                        # 全量
```

回滚任何时候：`DELETE FROM product_info WHERE product_id LIKE 'G%'`（同理删 `product_sku`、`product_property_value`、`aishop_stock.sku_stock`）。

---

## 五、已知缺口（按优先级）

| 缺口 | 原因 | 修复方向 |
|------|------|---------|
| **click 事件无调用方** | `log_click` 只有定义，没有 caller | 需前端商品卡片点击时携带 `(session_id, product_id)` 上报，或 Java 网关拦截 |
| **N5 Layer-2 无标注数据** | `02_catalog_seed.sql` 未生成，无商品可标 | 先跑数据，再用 `--emit-template` 生成标注模板，人工填 ~200 对 |
| **品牌偏好跨 session 丢失** | Redis profile 有 TTL，不同 session 不共享 | `agent_session_memory` 增加 `shopping_profile` JSON 列，每轮结束增量合并 |
| **refund howto 漏召** | refund-005/006/007 触发词覆盖不足（"在哪里点"/"怎么退"未在 howto object 列表）| 补充 `classifier.py` 的 howto 触发词 |
| **cancel 2 例受限于离线评测** | cancel-002/005 需要 `QUERY_ORDERS` 有真实返回 | 集成测试环境下可验证，离线无法修复 |

---

## 六、后续路线图

### 近期（接下来 1-4 周）

1. **装数据** — 执行第四节步骤，验证 `搜索"无线耳机"有结果` 
2. **接通 click 事件** — 最小实现：前端 `onClick` → POST `/api/agent/impression/click`；Agent 侧 `log_click` 已有但无调用方
3. **N5 Layer-2 标注** — `python benchmarks/run_search_relevance.py --emit-template` 生成模板，人工填 relevant_product_ids，跑 NDCG/MRR 建立搜索质量基线
4. **暴露行为 API** — `java_internal_client.py` 增加 `browse_history_list(user_id, limit)`、`favorite_list(user_id)`；对应 Java 侧补接口

### 中期（1-3 个月）

5. **LLM 查询理解 fallback** — 规则未命中时发 LLM 做 category/brand/scenario 提取，替代 taxonomy YAML 的边界盲区（延迟影响仅在规则缺口场景）
6. **结构化 profile 持久化** — `shopping_profile_service.merge_profiles`（已是纯函数）+ 数据库持久化，跨 session 积累偏好
7. **LLM 个性化重排** — `search_products` Top-10 后增加可选的 LLM listwise 重排步骤，传入 profile summary
8. **co-visitation i2i** — 从 `user_browse_history` + `user_product_favorite` 构造共现矩阵，结果存 Redis，接入 `load_similar_products` 第二路召回（有效交互 ≥5k 对后启动）

### 长期（3-6 个月）

9. **A/B 框架** — GrowthBook 自托管（MIT 协议，Docker），DAU ≥500 后接入，验证 LLM 重排 CTR 收益
10. **多步购物推理** — LangGraph 新增 `purchase_reasoning` 节点，识别"礼物/孩子/场景"类意图后主动询问细节

---

## 七、关键约束（始终有效）

- **Java 微服务侵入性最小**：改动优先在 Python Agent 侧；需要 Java 配合时只加接口，不改业务逻辑
- **小团队小数据场景**：所有方案评估以 MVP 现实为准，不引入特征平台、流式管道、GPU 训练等超出规模的基础设施
- **评测基线是地板**：`passRate` 只能涨不能降；改 taxonomy/classifier 前后都要跑 `pytest`
- **frozen benchmark discipline**：不能靠改测试集来提分；`knownFailures` 必须与实际失败双向相等；`datasetSha256` 不能变
- **数据标注上限**：模拟点击学到的是模拟器而不是真实用户；可验证的是 pipeline 正确性、NDCG 计算、A/B 管路，不可验证的是真实用户满意度

---

## 附录：参考文献（按主题）

| 主题 | 文献 |
|------|------|
| 混合搜索 | [Hybrid Search Done Right (ES)](https://ashutoshkumars1ngh.medium.com/hybrid-search-done-right-fixing-rag-retrieval-failures-using-bm25-hnsw-reciprocal-rank-fusion-a73596652d22) |
| LLM 商品扩写 | [PRAISE ACL 2025](https://aclanthology.org/2025.acl-demo.62/)；[LLM 产品描述扩写](https://ar5iv.labs.arxiv.org/html/2310.18357) |
| LLM 精排 | [LLM-Enhanced Reranking (2025)](https://arxiv.org/pdf/2507.16237.pdf)；[Zero-Shot Listwise Reranking](https://arxiv.org/html/2312.02724v1) |
| 对话式偏好引导 | [PEBOL](https://ar5iv.labs.arxiv.org/html/2405.00981)（10轮问答 MAP@10 +131%）；[Usage-Context Elicitation](http://arxiv.org/pdf/2111.13463v1) |
| 用户画像 | [LLM-TUP](https://arxiv.org/abs/2508.08454)；[ProfiLLM](https://arxiv.org/pdf/2506.13980v1) |
| A/B 实验 | [GrowthBook](https://www.ycombinator.com/companies/growthbook)；[Interleaving 实验](https://metricgate.com/blogs/interleaving-experiments-ranking-evaluation/) |
| i2i 协同过滤 | [Redis 协同过滤实现](https://redis.io/blog/collaborative-filtering-how-to-build-a-recommender-system/) |
| 查询改写 | [Multi-Task LLM E-commerce Query Rewriting (2025)](https://arxiv.org/html/2603.02555v1) |
| 对话式商务 | [Amazon Rufus + COSMO](https://www.amazon.science/blog/the-technology-behind-amazons-genai-powered-shopping-assistant-rufus) |

