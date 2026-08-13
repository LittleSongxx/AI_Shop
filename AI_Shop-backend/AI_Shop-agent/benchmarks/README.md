# AI_Shop 评测入口

> 内容状态：当前有效
>
> 本轮实施基线：`ef9aa0659a9275a99bb74cdb46e87770150dea0a`
>
> 最后核验时间：2026-08-13（Asia/Shanghai）
>
> 适用环境：确定性回归、本地集成和可选 live 评测；不是生产效果或真实用户报告

当前项目级证据入口为 [`docs/AI应用求职项目证据总览.md`](../../../docs/AI应用求职项目证据总览.md)，机器口径为 [`docs/evidence-manifest.json`](../../../docs/evidence-manifest.json)。本目录包含两代互补评测：

- `aishop_convo_v1` 等历史冻结集保留项目演进和规则回归证据。
- `aishop-eval/v1` 统一 Runner 输出 case、summary 和 Markdown 报告，覆盖 Commerce runtime、AI 安全、Search/RAG 与进程隔离消融。

统一 Runner：

```bash
python benchmarks/run_agentic_commerce_runtime.py --run-id local-commerce
python benchmarks/run_ai_safety.py --run-id local-safety
python benchmarks/run_search_rag_eval.py --run-id local-search-rag
python benchmarks/run_ablation.py --run-id local-ablation
```

成熟 Search/RAG 评测采用显式阶段，holdout 只有在配置冻结后才能执行：

```bash
python benchmarks/run_search_rag_mature_eval.py prepare
python benchmarks/run_search_rag_mature_eval.py collect-dev --run-id mature-local
python benchmarks/run_search_rag_mature_eval.py replay --run-id mature-local
python benchmarks/run_search_rag_mature_eval.py collect-final --run-id mature-local --finalize-holdout
python benchmarks/run_search_rag_mature_eval.py package --run-id mature-local
python benchmarks/run_rag_generation_eval.py --selection-version v2 --run-id mature-generation --top-k 10
```

`collect-*` 执行一次冷调用并保存查询向量、BM25/Vector Top-50、Rerank 完整顺序和阶段延迟；`replay` 的所有参数消融 Provider 调用数必须为 0。大数据、向量与完整 case 位于 Git 忽略的 `benchmarks/results/`，`package` 只提交多 K 指标、变体汇总、配对差值、bootstrap CI、badcase、原始 SHA 和诚实边界。本轮正式证据目录为 `benchmarks/evidence/search-rag-mature-v1/mature-21d8159/` 与 `benchmarks/evidence/rag-generation-live-v2/mature-rag-generation-21d8159/`，没有接受或覆盖 baseline。

结果写入 `benchmarks/results/<suite>/<run_id>/` 并被 Git 忽略；只有显式传入 `--accept-baseline` 的 Runner 才能写 `benchmarks/baselines/*.lock.json`，CI 不会自动接受 baseline。确定性结果必须写作 `SYNTHETIC`，Search/RAG 未使用 `--live` 时不得声称 Recall/MRR/NDCG 或真实模型成本。

## 历史冻结会话集 aishop_convo_v1

`scripts/eval_rag.py` 只评检索一层：给一句 query，看知识库有没有召回对的文档。
但线上一次会话真正会走错的地方在它前面——意图判错、该调工具却没调、
该转人工却自己答、注入话术当成正常需求。这些都在检索之外，之前没有任何评测覆盖。

这个目录补的就是那一层：**会话级、离线、可重复**的冻结评测集。

## 为什么是离线的

评的是 `resolve_intent(..., allow_llm=False)` 这条确定性路径，不是整条链路。
这样选有三个理由：

1. 这条路径本来就是线上主路径。`resolve_intent` 里 structural 和 rule_priority
   两档在 LLM **之前**执行，命中就直接返回；LLM 挂掉或超时时 rule fallback 又是唯一兜底。
   也就是说这层判错，模型再准也救不回来。
2. 离线才能真冻结。带 LLM 的评测每次跑分数都不一样，"跑一次记下来"就没有意义，
   而重跑取好成绩就是在给自己发奖。
3. 不依赖 Nacos / MySQL / Redis / Java 微服务，任何人 clone 下来就能复现同一个数字。

代价写在 `冻结会话评测限制与变更记录.md` 第 1 条：这不是端到端质量，模型那一层没被评到。

## 冻结的含义

- **数据集不可静默修改**：`aishop_convo_v1.jsonl` 的 SHA-256 写在
  `aishop_convo_v1.lock.json` 里，跑分时先校验。改了题面而没改 lock，runner 直接报错。
- **一次成型，不重采样**：每条 case 跑一次，结果就是结果。不存在"再跑一次看看"。
- **失败留着，不改标签**：期望值是按"正确的客服行为应该是什么"写的，不是按当前代码的
  输出写的。跑出来错的那些留在集合里、记在 `冻结会话评测限制与变更记录.md`，
  不把期望改成实际输出让分数变好看。

最后一条是这个评测集唯一有价值的地方。把 label 改成实际输出，任何实现都能得 100 分。

## 文件

| 文件 | 作用 |
|---|---|
| `aishop_convo_v1.jsonl` | 冻结题面，一行一条 case |
| `aishop_convo_v1.lock.json` | 数据集 SHA-256 + 首次跑分基线 + 已知失败 ID 清单 |
| `validate_convo_eval.py` | 题面结构体检：字段、分布、split、PII |
| `run_convo_eval.py` | 跑分 + 对基线做门禁 |
| `results/` | 跑分产物（summary / raw / failures） |
| `冻结会话评测限制与变更记录.md` | 这个评测集**没有**覆盖什么，以及留着的失败是什么 |

## 用法

```bash
cd AI_Shop-backend/AI_Shop-agent

.venv/bin/python benchmarks/validate_convo_eval.py     # 题面体检
.venv/bin/python benchmarks/run_convo_eval.py          # 跑分 + 门禁
.venv/bin/python benchmarks/run_convo_eval.py --write-results   # 另外落 results/
```

`tests/test_convo_eval_frozen.py` 在 `pytest` 里跑同一套逻辑，
所以数据集漂移、指标退步、已知失败被悄悄改标签，都会在普通测试里就炸。

## 订单售后 Episode 评测

`order_aftersales_episode_v1.jsonl` 和对应 lock 冻结了取消订单、未知远端结果、
工单待处理/人工解决、Verifier 失败与人工审核资格等事实轨迹。它只判断轨迹事实是否
完整以及是否经过人工批准，不合成单一 Reward，也不导出训练集：

```bash
.venv/bin/python benchmarks/run_episode_eval.py
```

人工审核生成的 ACTIVE Badcase 回归 Case 可通过以下命令重放；文本 Case 禁用 LLM，
Episode Case 复用同一个确定性 evaluator：

```bash
.venv/bin/python scripts/run_badcase_replay.py
```

端到端 WebSocket 性能门禁使用 `scripts/agent_performance_gate.py`。确定性模式默认
100 请求、并发 10；Live 模式默认 20 请求、并发 2。两者都要求至少提供与并发数相同
的测试用户 token，以遵守每用户每秒限流；确定性模式还需通过 `INTERNAL_TOKEN`
读取脱敏 Episode Trace，从而计算 queue wait。

## 门禁怎么判

runner 拿本次结果和 lock 里的基线比，两个方向都算失败：

- **变差**：出现基线里没有的失败 case，或指标低于基线。这是回归。
- **变好但没更新 lock**：已知失败被修好了。这也报错，因为 lock 是"当前事实"的记录，
  修好了就该把它从清单里划掉，而不是让清单继续挂着一条已经不存在的失败。

第二条是有意的。只在变差时报警的门禁，跑一段时间之后 lock 里会积一堆早就修好的
"已知失败"，那时候它记录的就不是事实了。
