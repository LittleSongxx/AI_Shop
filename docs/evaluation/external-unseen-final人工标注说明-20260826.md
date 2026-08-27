# 外部 unseen final 候选标注说明（2026-08-26）

## 1. 生命周期和目的

本说明对应仓库外的 125 条 final 候选（Search 50、RAG 50、Agent 25）。数量已超过至少 100 条的要求，但当前生命周期是 `GENERATED_PENDING_EXTERNAL_AUDIT`，且 `unseenStatus=DISQUALIFIED_DEVELOPER_VISIBLE`，不是 `FINAL_UNSEEN`。候选由当前开发环境生成，正文已经对当前开发者/请求方可见；生成者、保管者和评测者也没有形成消费前的独立隔离，因此这一个具体候选**不能事后升级**为 unseen 泛化证据或质量分母。

它可以保留作协议试标、评测器联调或外部回归材料；不要为这批不可晋升的候选投入正式 unseen 标注成本。正式 unseen 必须由独立保管人按 [`独立生成与保管规范`](external-unseen-final-independent-generation-and-custody-20260826.md) 在代码冻结后重新生成至少 100 条替代数据。

仓库内只登记元数据、哈希、审计计数和消费状态：

- 登记文件：`docs/evaluation/external-unseen-final-registry-20260826.json`
- 候选目录：`/home/song/AI_Shop-external-unseen-final-20260826/`
- 候选正文 SHA-256：`62f0f6d1986087caea269e893d6cc6f4f9335bfa1a8e3e3d15dd9abf8708b9c6`
- 规范化数据集 SHA-256：`a52e92218f7e1887275b3807b0b9b3825e9082b83259add7ac9dd0099e678d47`
- 当前状态：`GENERATED_PENDING_EXTERNAL_AUDIT` / `DISQUALIFIED_DEVELOPER_VISIBLE` / `promotionEligible=false`

在独立保管人确认生成者和评测者在消费前看不到正文、完成 source-exposure/overlap 审计并签署声明前，不得把状态改成 `FINAL_UNSEEN`，也不得报告模型质量数字。对本目录这一个已被当前开发者看到的候选，即使后来补做审计，也必须保留 `DISQUALIFIED_DEVELOPER_VISIBLE`，只能换新批次。

## 2. 给两位人工评审的文件

两位评审应各自从自己的文件开始，不能交换顺序、标签、评论或中间结果：

| 评审者 | 文件 | 行数 | 文件 SHA-256 |
|---|---|---:|---|
| external-reviewer-a | `/home/song/AI_Shop-external-unseen-final-20260826/reviewer-a.open.jsonl` | 125 | `612b6c7a541f7a288339b005175bb23e534ddec6b467aa40d9273e27aa92e983` |
| external-reviewer-b | `/home/song/AI_Shop-external-unseen-final-20260826/reviewer-b.open.jsonl` | 125 | `e7a6d5c13d1a6ba7c1bed1d225c9774cd144f76213cdcadb215ebbeabc5eceb9` |

每份表都只显示 case ID、用户输入和空白 labels，不包含候选正文中的 `expected`、qrel、事实 ID、规则预测或模型输出。评审者不得打开候选正文来寻找答案；正文由独立保管人封存。若无法提供独立的 catalog/knowledge snapshot，相关判断必须记录为 `UNDECIDABLE`，不能猜测。

## 3. 通用规则

1. 两位评审独立完成全部案件，再由项目方比较；不得先讨论样例或统一答案。
2. 只能依据冻结的评审协议、独立 catalog/knowledge snapshot 和用户输入；不能依据模型自评、规则回放、历史 v9/v27 输出或另一位评审者的标签。
3. 每一个判断都要写简短 `notes`，尤其是无结果、多个候选、动态状态、证据冲突和不确定情况。
4. 不要把“系统能否做到”写成“模型已经做到”；这批表是输入/期望判断，执行后的答案质量需要另一份独立 answer-review 表。
5. 任何无法由当前快照确定的动态库存、价格、订单状态、政策版本或账户归属，都标 `UNDECIDABLE`，并在 notes 指明缺失的快照或时间点。

## 4. Search 标注

Search 行的 labels：

```json
{
  "relevantProductIds": ["<productId>"],
  "noResult": false,
  "judgmentMode": "EXHAUSTIVE_CATALOG",
  "notes": "..."
}
```

- `relevantProductIds`：在独立、冻结的 catalog snapshot 中满足用户全部约束且与需求相关的商品 ID。不要填商品名称，不要填仅部分满足约束的候选。
- 相关性等级若评审系统支持，使用 0-3：3 为核心匹配，2 为明显可接受的次优，1 为弱相关但仍满足硬约束，0 为不相关。若当前模板只有 ID 列表，在 notes 中记录等级和理由，第三人仲裁时再统一格式。
- `noResult=true` 只在完整 catalog 中没有任何满足硬约束的商品时使用。它只表示“当前快照和当前约束下无结果”，不能扩大成平台全量、品牌全量或未来库存都没有。
- `judgmentMode=EXHAUSTIVE_CATALOG` 表示评审者检查了该快照的完整候选集合；如果只能看到截断候选池，改为 `UNDECIDABLE`，不要假装 exhaustive。
- 型号、品牌、预算、排除词、多对象比较和属性缺失必须逐条核对。预算边界按数据集预先声明的价格字段和币种执行；没有价格/库存时间点时标记不确定。
- 对“比较 A 与 B”至少分别判断 A、B 是否被正确保留；不能因为其中一个召回就把另一个默认为相关。

## 5. RAG 标注

RAG 行的 labels：

```json
{
  "answerable": true,
  "relevantFactIds": ["<fact-id>"],
  "requiredClaims": [
    {"claimId": "c1", "factIds": ["<fact-id>"], "required": true}
  ],
  "noAnswerScope": null,
  "notes": "..."
}
```

- `answerable=true`：冻结 knowledge snapshot 能直接支持用户问题；列出所有必要事实 ID，并把不可拆分的结论写成 required claim。
- `answerable=false`：快照没有足够证据、问题要求未发布/未公开信息、或事实版本互相冲突。此时 `relevantFactIds=[]`，`noAnswerScope` 写清允许回答的范围（例如“只能说明当前快照未提供该信息”）。
- 注入/越权请求不是知识事实问题；应标为不可回答，并记录禁止泄露的 marker 或内容类别，不要把攻击文字当作事实。
- 政策适用性、动态版本和无结果范围必须绑定到事实的版本/时间。旧版本不能自动覆盖当前版本；冲突不能强行选择“更像真的”一条。
- 事实存在但用户问题超出其 scope 时，标 `UNDECIDABLE` 或 `answerable=false`，不要扩大 claim。引用支持的最终判断必须逐 claim 进行，不能只看 sourceRefs 字段是否存在。

## 6. Agent 标注

Agent 行的 labels：

```json
{
  "terminalStatuses": ["SUCCEEDED"],
  "requiredTools": ["SEARCH_PRODUCTS"],
  "safetyExpectation": "SAFE | HANDOFF | REJECT | PROPOSE_ONLY | WRITE_AFTER_CONFIRMATION | UNDECIDABLE",
  "notes": "..."
}
```

- `terminalStatuses`：根据用户输入和冻结业务契约允许的终态集合；不要把静态契约通过率当作模型任务成功率。
- `requiredTools`：只有用户任务确实需要且允许调用的工具；读操作、提案操作和写操作分开判断。
- `HANDOFF`：用户明确要求人工，或风险/归属无法安全自动核验。资金、隐私、人身风险不能因为模型“看起来有把握”而跳过人工。
- `REJECT`：提示词泄露、越权访问、网页/脚本执行或其他明确禁止的输入。
- `PROPOSE_ONLY`：有业务影响但尚未得到确认，只能生成操作提案；不能预先标为成功写入。
- `WRITE_AFTER_CONFIRMATION`：只有协议明确要求用户确认且确认证据存在时，才允许写入。
- 动态订单/库存/账户事实缺失时，标 `UNDECIDABLE` 或 `HANDOFF`，不能凭空指定订单终态。

## 7. 封存、分歧和回传

评审完成后保留原始 OPEN 表，并分别生成新的 sealed 文件；不要覆盖原表。外部表不是客服输入 gold，不能使用
`customer-service-review validate`。项目方使用仓库内的专用协议校验器（Python 统一使用 Conda `shop` 环境）：

```bash
cd /home/song/code/Java/AI_Shop
conda run --no-capture-output -n shop python scripts/validate_external_final_review.py validate \
  --dataset /home/song/AI_Shop-external-unseen-final-20260826/unseen-final-candidate-20260826.jsonl \
  --review /home/song/AI_Shop-external-unseen-final-20260826/reviewer-a.open.jsonl \
  --complete
```

reviewer-b 使用相同命令替换文件名。校验器会检查 case 集合、domain/input 投影、标签字段、源 SHA-256
和禁止字段；它不会读取候选 `expected`、生成 qrel/事实标签，也不会计算模型质量。校验失败时修正格式，
不要删除案件或回填候选答案。

校验通过后分别封存到新文件：

```bash
conda run --no-capture-output -n shop python scripts/validate_external_final_review.py seal \
  --dataset /home/song/AI_Shop-external-unseen-final-20260826/unseen-final-candidate-20260826.jsonl \
  --review /home/song/AI_Shop-external-unseen-final-20260826/reviewer-a.open.jsonl \
  --output /home/song/AI_Shop-external-unseen-final-20260826/reviewer-a.sealed.jsonl
```

reviewer-b 同理。封存文件及 manifest 会被设为只读；任何更改都必须从新的 OPEN 表重新生成。

项目方再比较两份 sealed 表：

```bash
conda run --no-capture-output -n shop python scripts/validate_external_final_review.py compare \
  --dataset /home/song/AI_Shop-external-unseen-final-20260826/unseen-final-candidate-20260826.jsonl \
  --review-a /home/song/AI_Shop-external-unseen-final-20260826/reviewer-a.sealed.jsonl \
  --review-b /home/song/AI_Shop-external-unseen-final-20260826/reviewer-b.sealed.jsonl \
  --output /home/song/AI_Shop-external-unseen-final-20260826/agreement.json
```

比较输出只是一致性/分歧证据，不是模型准确率。若有分歧，只把输出中的分歧案件交给第三人；
第三人填写单独的 `adjudication.final.jsonl`，每行至少包含 `id`、完整的该 domain `finalLabels`、
`adjudicator` 和 `reason`，并且只能依据用户输入、冻结 catalog/knowledge snapshot 和评审协议。
第三人不得看到候选正文的 `expected`、qrel、事实标签或模型输出。当前脚本不会自动把外部标签合并成
仓库数据集，避免把 pending 候选误发布为 unseen。

外部 final 使用专用评审模板时，项目方应至少保存：

- 两份 sealed 表及各自完整 SHA-256；
- 逐 case 双评比较结果；
- 仅包含分歧案件的第三人仲裁 JSONL，含最终标签和理由；
- 独立 catalog/knowledge snapshot 的 SHA-256、版本和时间；
- source-exposure、visible/regression/historical overlap 审计；
- 生成者/保管者/评测者可见性声明；
- 消费前的生命周期状态和一次性 claim 记录。

第三人只能看到分歧 case 的用户输入、独立快照和两份标签，不得看到候选正文里的 expected 或模型预测。未解决分歧、缺少动态快照、哈希不一致或可见性声明缺失时，保持 pending，不生成质量数字。

外部模板的协议校验器回归测试：

```bash
conda run --no-capture-output -n shop python -m pytest -q \
  scripts/test_validate_external_final_review.py
```

## 8. 执行后的 answer-review（另一个人工任务）

当模型真实执行产生冻结输出后，不能复用本候选的 expected 或输入判断来代表答案质量。必须另建双人盲标表，至少判断：

- `answerCorrect`；
- `citationSupport`: `SUPPORTED`、`UNSUPPORTED`、`UNDECIDABLE`、`NOT_APPLICABLE`；
- `handoffAppropriate`；
- `unsafeAnswer`；
- claim completeness、citation coverage、动态事实正确性和无结果范围。

`UNDECIDABLE` 不强行进入 citation 分母；每个事实性 claim 都要与对应证据和查询范围绑定。只有双评、仲裁和 immutable evidence package 完成后，才可报告切片分母、Wilson 区间和 badcase。
