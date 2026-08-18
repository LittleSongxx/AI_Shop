# AI_Shop 评测入口

> 唯一规范入口：`python benchmarks/eval.py`
>
> 历史脚本说明：[`LEGACY.md`](LEGACY.md)
>
> 内容状态：当前有效
>
> 评测闭环重构基线：统一 `benchmarks/eval.py`；历史证据基线只读保留
>
> 最后核验时间：2026-08-17（Asia/Shanghai）
>
> 适用环境：确定性回归、本地集成和可选 live 评测；不是生产效果或真实用户报告

当前项目级证据入口为 [`docs/AI应用求职项目证据总览.md`](../../../docs/AI应用求职项目证据总览.md)，机器口径为 [`docs/evidence-manifest.json`](../../../docs/evidence-manifest.json)。本目录包含两代互补评测：

- `aishop_convo_v1` 等历史冻结集保留项目演进和规则回归证据。
- 历史 `aishop-eval/v1` 结果保留 case、summary 和 Markdown 报告；新的正式评测统一由 `benchmarks/eval.py` 编排。

## 规范闭环

所有正式 suite 共用同一生命周期：

```text
validate → preflight → known/execute → freeze → final/holdout → review → package → status
```

示例：

```bash
python benchmarks/eval.py list
python benchmarks/eval.py validate --suite search-v3
python benchmarks/eval.py preflight --suite search-v3 \
  --run-id search-v3-<git-sha>-<yyyymmdd>
python benchmarks/eval.py run --suite search-v3 --stage known \
  --run-id search-v3-<git-sha>-<yyyymmdd>
python benchmarks/eval.py run --suite search-v3 --stage final \
  --run-id search-v3-<git-sha>-<yyyymmdd> --finalize-holdout
python benchmarks/eval.py run --suite search-v3 --stage package \
  --run-id search-v3-<git-sha>-<yyyymmdd>
python benchmarks/eval.py status --suite search-v3 \
  --run-id search-v3-<git-sha>-<yyyymmdd>
```

`preflight` 不读取 fresh 数据、不创建 fresh claim；只有通过后才允许进入正式执行。最终结果分开报告 `execution`、`quality`、`provider`、`humanReview` 和 `evidence`，环境阻塞不会伪装成召回率为 0。正式 fresh 仍遵循 `ONE_SHOT_FAIL_RETAINED`，历史 evidence 不覆盖。Search/RAG/Agent 的领域评分器继续复用各自 adapter，但不再作为第二套公开生命周期。

## Agent v2 真实全栈任务成功率

`task_success_v2.jsonl` 是当前冻结契约：前 37 条与 `task_success_v1.jsonl` 逐对象相同，另有 7 条多步 sequence，共 44 条。sequence 覆盖 Mission 覆盖长期 Profile、候选比较与报价刷新、单/多主体视觉搜索、点击防伪、加购/两次成交/复购，以及退款、低分评价、售后联系和幂等。v1 数据、lock 与历史结果保持只读。

先做无凭据、无服务调用的哈希和结构校验：

```bash
python benchmarks/eval.py validate --suite agent-v2
```

正式运行没有模拟器或进程内 Graph 快捷路径。单轮请求必须经过 Agent API、RabbitMQ、Worker、LangGraph、MCP、Java 和数据库；sequence 还通过现有鉴权接口执行选图主体、点击、加购、下单、内部支付成功、退款、评价和 Agent 确认。判分读取持久 Episode、Mission、Profile、报价快照、推荐事件、Outcome Ledger、pending action、Java 购物车/订单终态。Preflight 会强制检查真实 LLM、生产 embedding、rerank、Java Gateway 和 MCP，缺失状态或 Provider 证据一律 fail-closed。

运行前需要满足以下契约：

- 以 `task_success_v2_bindings.example.json` 为字段模板，在 Git 忽略的本地文件中绑定真实 token、user、order、order item、SKU、地址和已审核图片资产；不得提交该文件。
- `AISHOP_EVAL_ISOLATED` 必须为 `enabled`；多主体降级用例还要求 `FAULT_PROFILE_VISUAL_PROVIDER_UNAVAILABLE=enabled`。这些标志只声明已经恢复隔离 fixture，不会在 Runner 内注入故障或创建公开测试后门。
- `FAULT_PROFILE_UNKNOWN_OUTCOME` 必须显式绑定为 `enabled`，并由测试环境为对应 fixture 注入未知远端结果。
- `AISHOP_INTERNAL_TOKEN` 只从环境变量或 CLI 私密参数读取，不写入 bindings、数据集或结果报告。
- API 与 Worker 必须使用同一个 `ORCHESTRATION_MODE`，修改模式后同时重启两个进程。
- 写操作会改变业务状态。每个模式运行前恢复同一份业务 fixture，并使用相同的 `--fixture-snapshot-id`。
- 真实 Provider、模型版本或依赖指纹不同的报告不得比较。

一次 adaptive 运行示例：

```bash
python benchmarks/eval.py preflight --suite agent-v2 \
  --run-id agent-v2-adaptive-<git-sha>-<yyyymmdd>
python benchmarks/eval.py run --suite agent-v2 --stage execute \
  --bindings benchmarks/task_success_v2_bindings.local.json \
  --run-id agent-v2-adaptive-<git-sha>-<yyyymmdd> \
  --fixture-snapshot-id fixture-agent-v2-<yyyymmdd> \
  --expected-orchestration-mode adaptive
```

三模式 live 消融需要分别把服务端 `ORCHESTRATION_MODE` 设为 `workflow`、`single_agent`、`multi_agent`，每次恢复同一 fixture、重启 API/Worker 后，通过统一入口运行：

```bash
python benchmarks/eval.py run --suite agent-v2 --stage execute \
  --bindings benchmarks/task_success_v2_bindings.local.json \
  --run-id agent-v2-workflow-<git-sha>-<yyyymmdd> \
  --fixture-snapshot-id fixture-agent-v2-<yyyymmdd> \
  --expected-orchestration-mode workflow

python benchmarks/eval.py run --suite agent-v2 --stage execute \
  --bindings benchmarks/task_success_v2_bindings.local.json \
  --run-id agent-v2-single-agent-<git-sha>-<yyyymmdd> \
  --fixture-snapshot-id fixture-agent-v2-<yyyymmdd> \
  --expected-orchestration-mode single_agent

python benchmarks/eval.py run --suite agent-v2 --stage execute \
  --bindings benchmarks/task_success_v2_bindings.local.json \
  --run-id agent-v2-multi-agent-<git-sha>-<yyyymmdd> \
  --fixture-snapshot-id fixture-agent-v2-<yyyymmdd> \
  --expected-orchestration-mode multi_agent
```

只有三份报告都达到 100% 执行与 Provider 完整性，且 dataset、case、fixture、模型集合和 Provider 指纹一致时，比较器才会输出配对差值与 bootstrap 95% CI：

```bash
.venv/bin/python benchmarks/compare_live_orchestration_ablation.py \
  --workflow benchmarks/results/task-success-live-v2/agent-v2-workflow-6eb8e8e-20260817/summary.json \
  --single-agent benchmarks/results/task-success-live-v2/agent-v2-single-agent-6eb8e8e-20260817/summary.json \
  --multi-agent benchmarks/results/task-success-live-v2/agent-v2-multi-agent-6eb8e8e-20260817/summary.json \
  --run-id agent-v2-ablation-20260817
```

正式面试 Trace 只能从已存在的真实 Episode 导出。一条必须是用户已确认且远端结果已知的 `CONFIRMED` 退款，另一条必须是用户已确认但远端结果未知、MySQL 处于 `INCONCLUSIVE` 或 `MANUAL_REVIEW` 的运行：

```bash
.venv/bin/python scripts/export_interview_traces.py \
  --success-refund-run-id <confirmed-run-id> \
  --unknown-outcome-run-id <unknown-run-id> \
  --bundle-id interview-20260817
```

当前 lock 的 `resultStatus` 为 `NOT_COLLECTED`。在上述完整条件实际满足前，不得把门禁阈值、单元测试或历史确定性结果写成 Agent live TSR、真实消融或正式 Trace。

## Search v3 与 RAG v5 正式门禁

两套新评测都使用带代码 SHA 和日期的 Run ID，先执行 known 并冻结配置，再显式打开一次性 fresh。下面的 `<release-version-v2>` 必须是管理端已激活且包含 `demo_knowledge_v2` 精确文档集合的不可变知识快照版本；运行期间不得切换 release、模型或 Provider。

Search v3：

```bash
python benchmarks/eval.py validate --suite search-v3
python benchmarks/eval.py preflight --suite search-v3 \
  --run-id search-v3-<git-sha>-<yyyymmdd>
python benchmarks/eval.py run --suite search-v3 --stage known \
  --run-id search-v3-<git-sha>-<yyyymmdd>
python benchmarks/eval.py run --suite search-v3 --stage final \
  --run-id search-v3-<git-sha>-<yyyymmdd> --finalize-holdout
python benchmarks/eval.py run --suite search-v3 --stage package \
  --run-id search-v3-<git-sha>-<yyyymmdd>
```

RAG v5 必须先准备 catalog v2 的隔离索引并完成检索门禁，再运行生成：

```bash
python benchmarks/eval.py validate --suite rag-v5
python benchmarks/eval.py preflight --suite rag-v5 \
  --run-id rag-v5-<git-sha>-<yyyymmdd>
python benchmarks/eval.py run --suite rag-v5 --stage retrieval-known \
  --run-id rag-v5-<git-sha>-<yyyymmdd> --release-version <release-version-v2>
python benchmarks/eval.py run --suite rag-v5 --stage retrieval-final \
  --run-id rag-v5-<git-sha>-<yyyymmdd> --release-version <release-version-v2> \
  --finalize-holdout
python benchmarks/eval.py run --suite rag-v5 --stage generation-known \
  --run-id rag-v5-<git-sha>-<yyyymmdd> --release-version <release-version-v2>
python benchmarks/eval.py run --suite rag-v5 --stage generation-final \
  --run-id rag-v5-<git-sha>-<yyyymmdd> --release-version <release-version-v2> \
  --finalize-holdout
python benchmarks/eval.py run --suite rag-v5 --stage package \
  --run-id rag-v5-<git-sha>-<yyyymmdd>
```

两名真人盲审仍使用 `human_review/rag_v5_review.py` 的 `merge`，但结果挂在同一个 `rag-v5/<run-id>` manifest 下。

生成完成后，`results/rag-v5/<run-id>/generation/human-review/` 中只有 20 条 fresh 的盲评材料。两名真人分别填写 `reviewer-a.csv` 与 `reviewer-b.csv`，再合并：

```bash
.venv/bin/python benchmarks/human_review/rag_v5_review.py merge \
  --package-dir benchmarks/results/rag-v5/rag-v5-6eb8e8e-20260817/generation/human-review \
  --reviewer-a <reviewer-a.csv> --reviewer-b <reviewer-b.csv> \
  --output benchmarks/results/rag-v5/rag-v5-6eb8e8e-20260817/generation/human-review/merged-review.json
```

fresh 执行锁位于对应 `results/` 根目录。它不是可删除后重试的缓存；失败时保留原 Run，将该集合转为下一版 known，并为下一版另建未见集。

历史 deterministic runner 已移入 [`LEGACY.md`](LEGACY.md)，不再作为正式 suite 入口。CI 兼容执行只能通过隐藏的 `legacy-deterministic` 注册项调用，不创建 fresh 证据。

历史 mature Search/RAG v1/v2 replay 命令见 [`LEGACY.md`](LEGACY.md)。它们只用于读取既有证据，不再作为新正式运行入口。

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
