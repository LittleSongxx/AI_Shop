# Text2SQL V0 独立评测

本目录是独立的 `DEVELOPMENT / PROVISIONAL` 评测器，不扩展既有 Search/RAG/Agent `Domain`，也不进入任何发布门禁。固定边界为：

- `development=true`
- `provisional=true`
- `unseen=false`
- `releaseGateEligible=false`
- 固定时钟 `2026-08-27 12:00:00 Asia/Shanghai`
- MySQL `8.4.11`，十个视图来自仓库真实 Admin migration
- V0 只覆盖当前十视图和最小 RBAC；不支持 Join、窗口函数、同比环比、正式财务口径或 verified query；确定性 compiler 目前只覆盖供应链、推荐质量和工具质量的受支持子集，其余视图仍受 governed LLM-SQL 路径约束

## 当前生命周期

AI 候选集仍保留在 `evaluation/datasets/text2sql/v0-candidates.jsonl`，不得直接作为正式基线。经 A/B 独立真人审核和 C 仲裁后的 gold 已封存在：

```text
AI_Shop-backend/evaluation-evidence/benchmarks/text2sql/
  gold-v0-20260828/adjudicated/
```

修复前、修复后及统一 scorer 的配对证据分别位于：

```text
pre-foundation-v0-20260828-run-001/
post-foundation-v0-20260828-run-002/
paired-pre-post-v0-20260828-run-002/
post-quality-compiler-v0-20260829-run-001/
post-quality-compiler-v0-20260829-run-002/
```

`post-foundation-v0-20260828-run-001` 永久保留；该包发现评测器把随机 `resultHash/resultSetId` 中偶然出现的 11 位数字误判为手机号，未修改旧包。修正 PII 检测投影并加入真假手机号回归测试后，重新运行生成 run-002。

两版 canonical 共 160 个输出的 A/B 双盲评审及 C 仲裁已完成并封存：

```text
answer-review-v0-20260828-a-sealed-001/
answer-review-v0-20260828-b-sealed-001/
answer-review-v0-20260828-adjudicated-001/
verification-v0-20260828-final-001/
final-v0-20260828-run-002/
```

reviewer 包不含修复前/后映射、自动分数或另一 reviewer 标签；只有 control 子目录保存版本绑定。A/B 是否进入 C 仲裁只比较 `decision`。本次 A/B 一致 155 项，C 仅仲裁 5 项。最终人工判断为修复前 `0/80 ACCEPT`、修复后 `29/80 ACCEPT`；仍有 51 个修复后输出被拒绝，因此不得将证据链完成解释为质量发布就绪。

## CLI

从 `AI_Shop-backend/AI_Shop-agent` 执行：

```bash
conda run --no-capture-output -n shop \
  python -m evaluation.text2sql.cli --help
```

常用的确定性命令：

```bash
# 验证 catalog、候选集与内容锁
python -m evaluation.text2sql.cli catalog-verify
python -m evaluation.text2sql.cli dataset-verify-lock

# 启动独立 tmpfs fixture，并用真实 migration 重建
python -m evaluation.text2sql.cli fixture-bootstrap --state base
python -m evaluation.text2sql.cli fixture-verify
python -m evaluation.text2sql.cli fixture-data-fingerprint

# 启动隔离 Agent/Admin 后，封存 Java RBAC + 签名转发冒烟证据
python -m evaluation.text2sql.cli runtime-start
python -m evaluation.text2sql.cli runtime-smoke
python -m evaluation.text2sql.cli runtime-stop

# 完成真人 SQL 后生成该 reviewer 自己的类型化 oracle
python -m evaluation.text2sql.cli review-materialize <reviewer-workspace>
python -m evaluation.text2sql.cli review-validate <reviewer-workspace> --complete

# 封存 A/B、比较分歧、第三人仲裁（输出目录必须不存在）
python -m evaluation.text2sql.cli review-seal <reviewer-workspace> <immutable-output>
python -m evaluation.text2sql.cli review-compare <sealed-a> <sealed-b> <comparison-output>
# 若有分歧，C 填完最终 SQL 后先物化其 oracle
python -m evaluation.text2sql.cli review-adjudication-materialize <comparison-output>
python -m evaluation.text2sql.cli review-adjudicate \
  <sealed-a> <sealed-b> <comparison-output> <gold-output>

# 只有 human gold 才允许执行；每版固定 80 × 3，trial 1 为 canonical
python -m evaluation.text2sql.cli baseline-run \
  --phase pre-foundation --dataset <gold-v0.jsonl> --output <new-evidence-dir>

# 使用同一版本 scorer 重算两版原始证据并生成配对报告
python -m evaluation.text2sql.cli baseline-compare \
  --pre <pre-evidence> --post <post-evidence> \
  --dataset <gold-v0.jsonl> --output <new-comparison-dir>

# 创建、校验、封存 canonical 输出的双盲人工评审
python -m evaluation.text2sql.cli answer-review-open \
  --pre <pre-evidence> --post <post-evidence> \
  --dataset <gold-v0.jsonl> --output <new-open-dir>
python -m evaluation.text2sql.cli answer-review-validate \
  <reviewer-open-dir> --review-file <completed-jsonl> --complete
python -m evaluation.text2sql.cli answer-review-seal \
  <reviewer-open-dir> <new-sealed-dir> --review-file <completed-jsonl>
python -m evaluation.text2sql.cli answer-review-compare \
  <sealed-a> <sealed-b> <control-dir> <new-comparison-dir>
python -m evaluation.text2sql.cli answer-review-adjudicate \
  <sealed-a> <sealed-b> <comparison-dir> <control-dir> <new-final-dir>

# 校验所有输入证据并生成固定为 DEVELOPMENT / PROVISIONAL 的最终报告
python -m evaluation.text2sql.cli final-report \
  --pre <pre-evidence> --post <post-evidence> --paired <paired-evidence> \
  --gold <adjudicated-gold> --answer-review <adjudicated-answer-review> \
  --verification <verification-evidence> --output <new-final-report-dir>
```

`fixture-down` 会停止并移除评测 Compose 的 tmpfs/临时资源；它不会操作仓库现有 `aishop-mysql` 或 Redis。

正式 runner 还会按 `runId` 只读采集隔离 `agent_run/agent_step`，保存 semantic plan、SQL
guard、EXPLAIN、DB 时间、模型调用/token/成本；DENY 安全题执行前后会比较八个源服务 schema
的全表数据指纹。如检测到写入，会保留失败证据、立即重建该 fixture state，避免污染后续样本。
分页只有在同一 `resultSetId` 且后续页没有 LLM/SQL 时才算同源；导出必须同时匹配
`resultSetId` 与冻结结果 hash。

Gold 中 `t2s-v0-007` 和 `t2s-v0-048` 的 `flow.fault=BRANCH_2_TIMEOUT` 目前只是声明性字段。
Text2SQL `baseline-run` 尚未把它映射到 branch-level fault capability 或注入事件；若实际观测到
branch 成功，必须如实记录 expected `PARTIAL` 未满足，不能把 `flow` 的无适用检查视为故障演练通过。

baseline 不接受单纯把 lifecycle 文本改成 `HUMAN_*` 的文件：Gold 必须位于带
`evidence.json`、冻结 catalog 和 `SHA256SUMS` 的封存目录中，两位 reviewer 身份、数据集 hash
和 release boundary 都会再次校验。

## 一致快照语法

MySQL 8.4 可执行语法是：

```sql
SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ;
START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY;
```

计划文本中的 `START TRANSACTION READ ONLY WITH CONSISTENT SNAPSHOT` 不是 MySQL 8.4 的合法排列；评测器使用上面的等价合法形式。生产配置若设置 `ANALYTICS_EVAL_FIXED_NOW` 会在启动校验中被拒绝。

reader 仍只拥有十个视图的 `SELECT + SHOW VIEW`。MySQL 8.4 对视图执行原生 `EXPLAIN` 还要求底层表的 `SELECT`，因此每个查询分支必须尝试 EXPLAIN，但确认的错误 1345 会记录为 `EXPLAIN_UNAVAILABLE_VIEW_PRIVILEGE`，`scanEstimate=null`，随后继续执行同一只读一致快照中的查询。其他 EXPLAIN 错误仍失败关闭；不会为取得执行计划而扩大源表权限。

## 证据边界

mutation diagnostic 只衡量 fixture 能否区分某些邻近错误 SQL，不是模型准确率。OPEN review、候选 oracle、单元测试、SQL guard 通过率和 Java→Agent 冒烟都不能替代双真人 gold 或 canonical 答案盲审。
