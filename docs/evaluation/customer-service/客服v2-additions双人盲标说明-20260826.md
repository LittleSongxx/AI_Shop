# 客服 v2 additions 双人盲标说明（2026-08-26）

## 1. 目的与当前状态

本说明用于 `customer-service-v2-additions` 的输入理解金标扩充。候选集共 60 条，覆盖 20 个 intent（每个 3 条），目标是在完成双人独立盲标、分歧仲裁和封存后，与现有 60 条 `HUMAN_VERIFIED` v1 合并为新的 120 条版本。

在人工流程完成前，候选集状态必须保持 `DRAFT_NEEDS_DUAL_HUMAN_REVIEW`，`releaseGateEligible=false`；不能使用候选文件中的 `expected`、构造时标签或任何模型预测作为金标，也不能更新当前质量分母。

候选源文件及其 SHA-256：

```text
AI_Shop-backend/AI_Shop-agent/evaluation/datasets/customer_service/candidate-v2-additions.jsonl
7110835813e32f6ed3823bd22c2e1487f7e9c2c62bb31d4eac92fb61f9bf4353
```

## 2. 交付给两位评审的文件

本轮交付副本放在仓库外，避免评审者直接编辑仓库模板：

| 评审者 | 待填写表 | 表 manifest | 顺序种子 | 当前状态 |
|---|---|---|---:|---|
| reviewer-a | `/home/song/AI_Shop-human-review-v2-20260826/reviewer-a.open.jsonl` | `/home/song/AI_Shop-human-review-v2-20260826/reviewer-a.open.jsonl.manifest.json` | `2026082601` | `OPEN` |
| reviewer-b | `/home/song/AI_Shop-human-review-v2-20260826/reviewer-b.open.jsonl` | `/home/song/AI_Shop-human-review-v2-20260826/reviewer-b.open.jsonl.manifest.json` | `2026082602` | `OPEN` |

两份表各 60 行，显示 `id` 和用户原话，`labels` 的五个字段均为空。表 manifest 已绑定源数据哈希和不同的随机顺序；请不要互换文件或修改 `reviewerId`。

评审者应分别在自己的副本中填写，不能查看另一位的表、候选源文件的 `expected` 字段、规则预路由输出、历史金标、模型自评或其他提示答案。评审完成后只回传填写后的 JSONL 和同名 manifest，保留原始文件，不要把回传文件放入 Git。

## 3. 每行需要填写的字段

每行 `labels` 必须完整填写以下字段；不能只填自己有把握的字段，也不能用空字符串代替未知：

```json
{
  "intent": "<一个冻结 taxonomy 值>",
  "riskLevel": "LOW | MEDIUM | HIGH",
  "shouldHandoff": true,
  "handoffSeverity": "NORMAL | CRITICAL | null",
  "slots": {"<slot key>": "<原话中的值>"}
}
```

`handoffSeverity` 只有在 `shouldHandoff=true` 时填写；不转人工时必须为 `null`。`slots` 没有明确实体时填 `{}`，不能填 `null`。

### 3.1 intent taxonomy

| intent | 判定边界 |
|---|---|
| `ADDRESS_CHANGE` | 修改收货地址、地址簿或询问下单后的地址变更 |
| `AFTERSALES_UNKNOWN` | 已发生售后问题但无法明确归为破损、错发、漏发、退款等类别 |
| `CANCEL_ORDER` | 取消订单、询问取消条件或取消流程；仅查询订单状态不归此类 |
| `CHAT` | 闲聊、问候、日期等不涉及购物任务的对话 |
| `COMPLAINT` | 对服务、商品或履约表达投诉/不满，且主要诉求是投诉 |
| `CONFIRM_RECEIPT` | 确认收货、签收状态或收货确认操作 |
| `DAMAGED_OR_WRONG_ITEM` | 商品破损、错发、颜色/型号不符等到货异常 |
| `HUMAN_REQUEST` | 明确要求人工客服或要求转人工，而非只询问一般规则 |
| `INVOICE` | 开票、发票抬头、税号或电子发票问题 |
| `PAYMENT_ISSUE` | 支付失败、重复扣款、扣款状态或支付异常；退款到账查询归 `REFUND_STATUS` |
| `PRODUCT_CONSULT` | 询问商品参数、材质、适配、使用场景或差异，不要求检索候选列表 |
| `PRODUCT_REVIEW` | 评价、追评、评价入口或评价内容规范 |
| `PRODUCT_SEARCH` | 要求查找/筛选商品，包含预算、品牌、属性或排除条件 |
| `QUERY_COUPON` | 查询优惠券领取、使用、面值、门槛或有效期 |
| `QUERY_FULFILLMENT` | 询问发货时间、预售、出库或履约时效 |
| `QUERY_LOGISTICS` | 查询物流轨迹、物流停滞、运单或配送状态 |
| `QUERY_ORDER` | 查询订单存在性、状态或订单列表，不包含取消/退款等更具体动作 |
| `RECOMMENT` | 请求推荐或重新推荐商品；已有明确检索条件但主要动作是推荐仍归此类 |
| `REFUND` | 发起/询问退货退款条件、退款申请或退款规则 |
| `REFUND_STATUS` | 查询已经申请的退款是否到账、处理进度或退款结果 |

遇到两个 intent 都似乎成立时，按用户最后明确的可执行诉求判定；若主要是“转人工”，使用 `HUMAN_REQUEST`。把冲突写入 `comment`，不要新增 taxonomy 值。

### 3.2 riskLevel

- `LOW`：一般商品咨询、物流/订单查询、优惠券和评价规则等，不直接造成资金、隐私或人身风险。
- `MEDIUM`：普通退款/取消、支付状态、地址修改、售后纠纷、投诉或可能影响履约的请求，需要谨慎核验但没有明显紧急危险。
- `HIGH`：未经授权扣款、账户/隐私泄露、疑似欺诈、充电设备鼓包发热等安全风险，或用户明确要求立即人工处理的高后果事件。

风险按用户原话和当前请求判定，不因为“订单”或“金额”单独出现就自动标高；也不要把模型回复、订单后台状态或候选 `expected` 当作风险依据。

### 3.3 shouldHandoff 与严重度

`shouldHandoff=true` 的条件包括：用户明确要求人工；问题涉及高风险且需要人工核验/处置；或当前信息不足、重复失败、跨账户/订单归属不明，继续自动处理会产生不可逆后果。

- `CRITICAL`：正在发生或可能立即扩大的资金、账号/隐私或人身安全风险，例如未授权扣款、设备鼓包发热、敏感信息暴露。
- `NORMAL`：常规投诉、复杂售后、重复未解决问题或用户主动要求人工，但没有立即危险。

单纯询问“能否转人工”属于 `HUMAN_REQUEST`，并按用户是否要求立即转接填写 `shouldHandoff`；不能因为所有客服问题都可转人工而全部标 `true`。

### 3.4 slots

只标注用户原话中明确出现、且与该诉求有关的实体；不推断未说出的订单、商品、金额或数量。推荐使用以下键：`orderId`、`orderItemId`、`productId`、`productName`、`brand`、`amount`、`quantity`、`discount`。若原话有其他稳定实体，可以使用简短、可解释的键，并在 `comment` 说明。

- 值按原话保留，包括大小写、全角字符、货币符号、千分位和单位；不要把 `￥１，２９９．００` 改写成 `1299`。
- 订单号、商品型号和商品名按连续原文提取；不要把泛称（如“耳机”）擅自扩展为具体型号。
- 同一键出现多个值时，按出现顺序用全角分号 `；` 连接，并在 comment 写明有多个值；不要丢弃第二个值。
- 没有明确槽位时填 `{}`；不要把预算、品牌或数量藏在 comment 中而不放进 `slots`。
- `slots` 的键和值必须是非空字符串；不要填 `null`、猜测值、后台查到的值或模型预测值。

## 4. 盲标与分歧处理

1. 两位评审者先各自完成全部 60 条，期间不得交换意见或共享中间结果。
2. 填写后在仓库根目录运行完整性校验（Python 必须使用 Conda `shop` 环境）：

```bash
cd /home/song/code/Java/AI_Shop
conda run --no-capture-output -n shop python -m evaluation.cli customer-service-review validate \
  --dataset AI_Shop-backend/AI_Shop-agent/evaluation/datasets/customer_service/candidate-v2-additions.jsonl \
  --review /home/song/AI_Shop-human-review-v2-20260826/reviewer-a.open.jsonl \
  --complete
```

对 reviewer-b 使用相同命令替换文件名。校验失败时修正格式，不要删除案件。

3. 校验通过后分别封存到新文件；不要覆盖 `OPEN` 表：

```bash
conda run --no-capture-output -n shop python -m evaluation.cli customer-service-review seal \
  --dataset AI_Shop-backend/AI_Shop-agent/evaluation/datasets/customer_service/candidate-v2-additions.jsonl \
  --review /home/song/AI_Shop-human-review-v2-20260826/reviewer-a.open.jsonl \
  --output /home/song/AI_Shop-human-review-v2-20260826/reviewer-a.sealed.jsonl
```

reviewer-b 同理。封存后不要再改 sealed 文件；任何改动都必须重新生成新的 sealed artifact。

4. 项目方比较两份 sealed 表并生成 `PENDING_ADJUDICATION` 证据。只有存在字段分歧的 case 交给独立第三人；第三人只看该 case 的用户原话和两份标签，不看候选 `expected` 或模型预测，填写 `finalLabels` 和具体理由。

5. 两份 sealed 表、分歧比较结果、仲裁 JSONL、合并数据集和各自 SHA-256 全部保存后，才可生成新的 120 条 `HUMAN_VERIFIED` evidence package。任何未解决分歧、缺失 manifest 或哈希不一致都必须阻断合并。

## 4.1 人工回传后的 120 条合并命令

两份 sealed 表和仲裁完成后，先用 `merge` 生成 additions 自身的 60 条
`HUMAN_VERIFIED` 数据集及 evidence。然后用下面的 `combine-v2` 命令校验 immutable v1
manifest、additions merge evidence、两个数据集的完整 SHA-256 和 ID 不重叠，再写出新的
120 条包。命令只接受已完成的人工链路；不能用候选源文件或任一 OPEN 表代替
`--additions-dataset`。

```bash
cd /home/song/code/Java/AI_Shop/AI_Shop-backend/AI_Shop-agent
conda run --no-capture-output -n shop python -m evaluation.cli customer-service-review combine-v2 \
  --additions-dataset /path/to/customer-service-v2-additions-human.jsonl \
  --additions-evidence /path/to/customer-service-v2-additions-merge.evidence.json \
  --output-dataset /path/to/customer-service-human-v2.jsonl \
  --output-manifest /path/to/customer-service-human-v2.jsonl.manifest.json \
  --evidence /path/to/customer-service-human-v2.evidence.json
```

输出包会明确标记 `qualityMetricsStatus=NOT_COMPUTED`。随后必须在这 120 条同一分母上
重新执行真实生产路径，并重新完成相应的人工答案/引用审查；不能从 v1 复制任何点估计、
区间或 badcase。`combine-v2` 本身只组装输入 gold，不产生模型质量数字。

## 5. 质量口径与禁止事项

- 本表测的是客服输入理解/路由标签，不是 HTTP 最终答案质量；不能从这些标签推导答案正确率、引用支持、CSAT、FCR 或线上成功率。
- 不得用规则 Macro-F1、静态行为契约、模型自评、历史 v27/v31 输出或另一位评审者标签辅助填写。
- 对用户原话无法唯一决定的 intent、风险或槽位，优先选择 taxonomy 中最保守且有原文依据的标签，并在 comment 标明边界；不要编造事实。若项目方在仲裁阶段无法根据原话和冻结规则决定，应将该字段记录为待仲裁，而不是擅自放入主分母。
- v2 合并后仍需重新运行分层指标和 Wilson 置信区间；不能把 60 条 v1 的指标复制给新增 60 条，也不能用评审一致率当模型准确率。

## 6. 外部 unseen final 的人工边界

仓库外 unseen final 是另一条证据链，不与 v2 additions 合并。其正文、qrel/事实标签和运行输出不得进入仓库、README、测试、fixture、历史报告或可搜索源码。正式批次必须遵循 [`独立生成与保管规范`](../external-unseen-final-independent-generation-and-custody-20260826.md)。只有独立保管人完成 source-exposure audit、development/regression/历史 final overlap audit，并确认生成者与评测者在消费前不可见，生命周期才能从 `GENERATED_PENDING_EXTERNAL_AUDIT` 改为 `FINAL_UNSEEN`；当前仓库外 125 条候选已经对开发者可见，只能作试标/联调，不能事后升级。

在独立保管和审计完成前，任何外部候选只能报告规模、哈希、切片和审计状态，不能报告模型质量数字或宣称真正 unseen 泛化。
