# 独立 unseen final 生成与保管规范（2026-08-26）

## 0. 先说结论

仓库外候选目录 `/home/song/AI_Shop-external-unseen-final-20260826/` 目前有
125 条输入（Search 50、RAG 50、Agent 25），数量已经超过“至少 100 条”的要求，
但这批具体正文在当前开发环境中生成，并且已经对当前开发者/请求方可见。因此它的
生命周期只能是 `GENERATED_PENDING_EXTERNAL_AUDIT`，`unseenStatus` 必须是
`DISQUALIFIED_DEVELOPER_VISIBLE`，`promotionEligible=false`。源码词法暴露和
overlap 审计为 0 只能说明没有发现那些机械重复，不能把这批候选事后升级为
`FINAL_UNSEEN`。

这批候选可用于协议试标、评测器联调和外部回归；不能用于 unseen 泛化率、线上准确率、
CSAT、FCR 或生产 SLO。要取得正式 unseen 结论，必须在代码冻结后重新生成一批替代
数据，并由独立保管人持有正文和事实标签。

## 1. 角色隔离

正式批次至少需要四个相互独立的角色。一个人可以承担多个行政角色，但不能同时拥有
“生成正文/事实标签”和“在消费前评测或报告结果”的权限。

| 角色 | 可见内容 | 不可做的事 |
|---|---|---|
| 生成者 | 代码冻结 commit、独立 catalog/knowledge snapshot、生成脚本 | 不把正文、expected/qrel、事实标签写入仓库或交给评测者 |
| 保管者 | 完整正文、expected/qrel/事实标签、seed、快照和哈希 | 不在消费前向当前开发者或模型评测进程透露答案 |
| 评测执行者 | 代码/运行配置、数量、slice 配额和哈希；消费时才得到输入 | 不读取 expected/qrel，不修改候选或标签 |
| 人工评审者 | 自己的盲标表、用户输入和独立快照 | 不看候选正文答案字段、不交换标签、不替模型自评 |

如果任一角色在消费前看到了完整候选正文或答案标签，立即把该批次标为
`DISQUALIFIED_DEVELOPER_VISIBLE`（或相应的 `DISQUALIFIED_*`），保留审计记录并重新
生成替代批次；不能通过改文件名、改 hash 或补一份声明恢复 unseen 资格。

## 2. 代码冻结和生成

1. 在生成前把待测代码冻结到干净 commit/tag。记录完整 commit、工作树是否干净、
   Python/依赖版本和生成时间；生成之后不能用新代码悄悄替换该指纹。
2. 由独立生成者在仓库外的受控目录生成正文。生成脚本不能把具体问题字符串、商品
   名、事实答案或 qrel 作为源码常量、注释、fixture、日志或提交内容留下。
3. 同时冻结独立 catalog/knowledge snapshot，并记录版本、时间、文件数和完整 SHA-256。
   动态价格、库存、订单、政策版本或账户状态必须带时间点；没有快照就只能让评审标
   `UNDECIDABLE`，不能猜测。
4. 每批至少 100 条。建议的最低分层是 Search 40、RAG 40、Agent 20；应覆盖型号/品牌、
   预算、否定约束、比较、多对象、无结果、部分 Provider、注入/越权、动态事实、
   handoff/risk、确认后写入和幂等。每个 slice 预先登记数量，不能在看到结果后改配额。
5. 为每条生成唯一且不可复用的 ID，统一使用 `aishop-evaluation-case/v3`、`split=final`。
   输入与 expected 分开保存；评测者首先只能得到 `id/domain/input` 投影。

## 3. 绑定和完整性

保管者必须保存一份只读 registry，至少包含：

- `datasetId`、版本、生命周期、数量和 Search/RAG/Agent 分层；
- 原始 JSONL SHA-256 和按 ID 排序规范化后的 dataset SHA-256；
- 代码 commit/tag、catalog/knowledge snapshot hash、provider/model/config fingerprint；
- seed、生成时间、保存位置、保管者和一次性消费状态；
- 两份盲标表的 reviewer ID、路径、完整 hash 和 manifest hash；
- source-exposure、visible/development/regression/historical overlap 审计结果；
- `allowedMetricsAfterAttestation`、`forbiddenClaimsBeforeAttestation` 和替代数据关联。

目录中的 `SHA256SUMS` 必须列出除自身以外的每个文件。任何正文、标签、快照、评审表
或 manifest 改动都要生成新的版本和新的哈希，不能原地修补 sealed artifact。

## 4. 消费前审计

保管者在把输入交给执行者前完成以下检查，并把安全摘要交给项目方：

1. schema、ID 唯一性、split/domain、输入结构和 slice 配额；
2. 对仓库源码、README、注释、测试、fixture、历史 report、提交记录和可见运行文本
   执行 source-exposure audit。审计报告只保留 case ID、位置和匹配 hash，不回显输入正文；
3. 对 development、regression、历史 final 和已消费批次执行规范化输入/ID overlap audit；
4. 检查所有 expected/qrel/事实标签都只在保管者目录，且评测器不会把它们写入日志或输出；
5. 检查生成者、保管者、评测者的可见性声明和权限；声明缺失时保持 pending。

任何暴露、重复、哈希不一致、快照缺失或权限不明都阻断 `FINAL_UNSEEN`。审计“没有
发现问题”不是独立保管证明，必须与角色隔离和可见性声明同时成立。

## 5. 人工标注和执行生命周期

输入期望标签与模型答案质量是两条不同证据链：

```text
candidate + independent snapshot
  -> two OPEN blind input-review sheets
  -> validate --complete
  -> two SEALED sheets
  -> compare
  -> third-person adjudication for disagreements
  -> final input/qrel/fact-label package

frozen model outputs
  -> separate two-person answer-review sheets
  -> adjudication
  -> immutable quality evidence package
```

评审表不得包含 `expected`、`predicted`、`modelOutput` 或自评字段。`UNDECIDABLE` 必须
保留在原字段，不能为了得到完整分母而改成通过/失败。比较结果中的一致率、Cohen κ
和分歧数只是协议可靠性，不能当作模型质量。

外部输入表使用仓库内的专用校验器：

```bash
cd /home/song/code/Java/AI_Shop
conda run --no-capture-output -n shop python scripts/validate_external_final_review.py validate \
  --dataset /path/to/held-by-custodian/final.jsonl \
  --review /path/to/reviewer-a.open.jsonl --complete
conda run --no-capture-output -n shop python scripts/validate_external_final_review.py seal \
  --dataset /path/to/held-by-custodian/final.jsonl \
  --review /path/to/reviewer-a.open.jsonl \
  --output /path/to/reviewer-a.sealed.jsonl
```

对 reviewer-b 重复后再 `compare`。校验器只验证绑定、结构、空标签/完整标签和泄漏
字段，不会从 candidate 的 expected 生成答案，也不会计算模型指标。

## 6. 晋升和可报告指标

只有以下条件全部满足，替代批次才可从 pending 晋升为 `FINAL_UNSEEN`：

- 代码 commit/tag、catalog/knowledge snapshot、provider/model fingerprint 和全量 hash
  已冻结；
- 生成者与保管者已完成 source-exposure/overlap 审计，且评测者在消费前不可见正文；
- 双人输入/qrel/事实标注已封存，分歧已由独立第三人仲裁；
- 模型真实执行输出、双人 answer-review、仲裁和 immutable evidence package 已完成；
- 每个 slice 都有真实分子、分母、badcase 和适用的 Wilson/其他预注册区间；
- registry 记录一次性消费，之后禁止重复挑选结果或改写分母。

在晋升前禁止声称 `FINAL_UNSEEN`、unseen generalization、online accuracy、CSAT、
FCR、production SLO。晋升后也只能报告预先登记且有真实人工依据的指标，例如 Search
Recall/MRR/NDCG、RAG answer correctness/citation support、Agent task success；不能把
规则门禁、静态 contract、结构化引用存在、模型自评或短时本机延迟写成语义质量。

## 7. 当前批次的交接动作

- 客服 v2 additions 的 60 条盲标表先由两名独立人工完成；未回传前保持
  `DRAFT_NEEDS_DUAL_HUMAN_REVIEW`，不能生成 120 条 `HUMAN_VERIFIED` 或新的质量分母。
- 当前 125 条外部候选只用于协议试标/联调。若要形成正式 unseen final，应由独立保管者
  按本规范重新生成至少 100 条替代数据，并把正文、expected/qrel/事实标签留在其受控
  目录；当前候选不能作为替代批次。
- 两条工作流不能合并，不能把 v2 的输入标签当作外部 Search/RAG/Agent 的 qrel，也不能
  把外部候选的输入判断当作客服答案质量。

