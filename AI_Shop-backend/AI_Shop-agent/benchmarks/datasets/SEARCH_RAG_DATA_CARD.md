# Search / RAG 评测数据卡

## 用途

`search_holdout_v1.jsonl` 与 `rag_holdout_v1.jsonl` 用于检查 AI_Shop 的搜索和 RAG 回归。它们与已有公开回归集分开统计，避免在修改公开 case 后继续沿用旧分数。

本数据卡只支持“本地可复跑评测”表述，不支持生产流量、真实用户效果、CTR/CVR、GMV 或线上 SLO 结论。

## 数据来源与标注

- 商品搜索标签由开发者人工核对 `data/simlect_catalog/catalog.json` 中锁定的 47 件镜像商品得到，使用 1–3 级相关性：3 为直接满足查询，2 为同类但约束匹配较弱。
- RAG 标签只引用 `data/03_smarlect_demo_seed.sql` 中 FAQ 9001–9006，以及 `data/demo_knowledge/*.md` 中实际存在的标题和正文。
- `no_answer` case 刻意询问知识库未发布的信息，用于测量拒答准确率。
- `injection` case 使用合成攻击文本，用于测量检索结果是否仍遵循锁定证据或拒答。它不包含真实用户数据、凭证或生产 secret。
- holdout 问题在创建时未出现在 `benchmarks/search_relevance_v1.jsonl` 或 `scripts/rag_golden.jsonl`，但文件随仓库公开，因此不是保密测试集。

## 划分与防污染

- `public`：已有回归集，适合日常开发和问题定位。
- `holdout`：独立改写问题，结果单独报告。本轮不得根据 holdout 失败直接改标签来抬高分数；需要修复检索逻辑或在评审后发布新数据版本。
- 数据集、商品目录、知识文件和 taxonomy 均通过 SHA-256 锁定。Runner 检测到哈希、case 数量、ID 唯一性或引用目标不一致时直接失败。

## 指标口径

- `Recall@K`：每个可回答 case 的相关证据召回比例，再做宏平均。
- `MRR`：首个相关结果排名倒数的宏平均。
- `NDCG@K`：按相关性等级（搜索）或二元证据相关性（RAG）计算的归一化折损累计增益。
- `citationCorrectness`：返回引用中，同时匹配标注来源且包含答案关键词的引用比例。
- `citationCoverage`：标注来源中，被有效引用覆盖的比例。
- `noAnswerAccuracy`：知识库无答案 case 中正确拒答的比例。
- `injectionRobustness`：标记为 `injection` 的 case 中，仍检索到正确证据并带有效引用，或正确拒答的比例。

## 已知限制

- 商品目录规模小，人工标签不覆盖所有可能的替代商品。
- 引用正确性使用来源标识与关键词启发式，不等同于人工逐句事实核查。
- 离线 deterministic 层只验证数据契约和查询理解，不代表 Elasticsearch、向量召回、重排或真实模型效果。
- live 层依赖本地 Search/Redis/Elasticsearch/知识发布状态；服务缺失必须报告为未执行或失败，不能用 deterministic 结果代替。
