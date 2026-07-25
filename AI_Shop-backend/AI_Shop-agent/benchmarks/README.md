# 冻结评测集 aishop_convo_v1

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

代价写在 `KNOWN_LIMITATIONS.md` 第 1 条：这不是端到端质量，模型那一层没被评到。

## 冻结的含义

- **数据集不可静默修改**：`aishop_convo_v1.jsonl` 的 SHA-256 写在
  `aishop_convo_v1.lock.json` 里，跑分时先校验。改了题面而没改 lock，runner 直接报错。
- **一次成型，不重采样**：每条 case 跑一次，结果就是结果。不存在"再跑一次看看"。
- **失败留着，不改标签**：期望值是按"正确的客服行为应该是什么"写的，不是按当前代码的
  输出写的。跑出来错的那些留在集合里、记在 `KNOWN_LIMITATIONS.md`，
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
| `KNOWN_LIMITATIONS.md` | 这个评测集**没有**覆盖什么，以及留着的失败是什么 |

## 用法

```bash
cd AI_Shop-backend/AI_Shop-agent

.venv/bin/python benchmarks/validate_convo_eval.py     # 题面体检
.venv/bin/python benchmarks/run_convo_eval.py          # 跑分 + 门禁
.venv/bin/python benchmarks/run_convo_eval.py --write-results   # 另外落 results/
```

`tests/test_convo_eval_frozen.py` 在 `pytest` 里跑同一套逻辑，
所以数据集漂移、指标退步、已知失败被悄悄改标签，都会在普通测试里就炸。

## 门禁怎么判

runner 拿本次结果和 lock 里的基线比，两个方向都算失败：

- **变差**：出现基线里没有的失败 case，或指标低于基线。这是回归。
- **变好但没更新 lock**：已知失败被修好了。这也报错，因为 lock 是"当前事实"的记录，
  修好了就该把它从清单里划掉，而不是让清单继续挂着一条已经不存在的失败。

第二条是有意的。只在变差时报警的门禁，跑一段时间之后 lock 里会积一堆早就修好的
"已知失败"，那时候它记录的就不是事实了。
