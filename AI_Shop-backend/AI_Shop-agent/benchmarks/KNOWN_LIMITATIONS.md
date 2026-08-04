# Known Limitations — aishop_convo_v1

基线：120 条 case，通过 120（1.0）。dev 1.0 / test 1.0。
数据集 SHA-256 `403d7214e28e3146…`（完整值见 lock）。

> 基线走过 90（0.803571）→ 96（0.857143）→ 99（0.883929）→ 112（1.0）→ 120（1.0）。
> 90→99→112 的每一段，题面一个字没动（SHA-256 全程不变），所以升的是实现而不是标准。
> 112→120 是**加题不是改题**（2026-08-03）：新增 8 条 case 覆盖三档新能力——
> A2 会话级意图延续（`cont-001`~`cont-004`）、A3 死循环转人工（`loop-001/002`）、
> A1 Verified-Action 业务状态核对（`verified-001/002`），原 112 条的期望值一字未改。
> 2026-08-03 随后修正 `loop-001/002` 的上下文表示：`recentIntents` 按线上实现只含
> 历史轮次，因此三连样本应注入两轮历史、两连反例应注入一轮历史；用户输入和期望
> 标签未改。这项修正同时暴露并修复了原实现直到第 4 轮才触发的 off-by-one。
> 分段的成因与修法逐条记在各节末尾。
> 每批都已按 lock 的双向对齐规则移出下面的清单。

**当前 knownFailures 为空。** 期望值始终按"正确的客服行为应该是什么"写，不是按当前实现的
输出写；跑出来不对的没有改成实际输出，也没有重跑取好成绩。把 label 对齐到实现，
任何实现都能得 100 分，那样这个集合就一点用都没有了。

`benchmarks/aishop_convo_v1.lock.json` 里有机器可读记录。
两边由 `tests/test_convo_eval_frozen.py` 双向对齐：lock 里有的这里必须有，
这里挂着的不能是已经修好的。

下面这个块是给测试读的，必须和 lock 的 `knownFailures` 完全相等。
正文里可以随便引用通过的 case 做对照（比如"`cancel-004` 是通过的"），
所以对照不能靠全文搜 case id，只能靠这一个块。

<!-- KNOWN_FAILURE_IDS:BEGIN -->
```
```
<!-- KNOWN_FAILURE_IDS:END -->

---

## 一、这个评测集没有覆盖什么

1. **不是端到端质量。** 评的是 `resolve_intent(..., allow_llm=False)` 这条确定性路径，
   LLM 那一层完全没评到。线上大部分轮次是 LLM 给意图，规则只在 LLM 之前（structural /
   rule_priority）和之后（rule fallback）起作用。所以这里的 1.0 **不能**读成
   "线上意图准确率 100%"。
2. **没有真实检索和真实工具结果。** MCP 出网、Java 微服务、Redis 全是桩，
   退款那条链路的订单项数据由题面的 `fixture` 字段喂。所以"参数拼对了"不等于
   "线上这次调用会成功"。
3. **没有真实多轮交互。** 每条 case 只有一句用户输入加可选的咨询卡上下文；
   2026-08-03 起支持**会话级上下文注入**（`context.sessionIntent` 上一轮意图、
   `context.recentIntents` 最近轮次意图），评的是"带上一轮事实的意图决策"，
   但真正的多轮指代消解、话题切换、上下文污染仍然没评（那是 τ-bench 形态的工作）。
4. **没有生成质量。** 回答文本好不好、有没有幻觉、有没有把工具结果说错，
   这些一条都没评——这个集合只看"路由到哪、调不调工具、参数是什么"。
5. **规模小。** 120 条，最小的 subset 只有 4 条（`continuation`；`confirm_receipt` 有 5 条）。
   单个 subset 的通过率波动一条 case 就是 20 个点，不适合拿来细粒度比较。
6. **只有中文。** 规则表全是中文关键词，英文输入的表现除了注入那几条之外没有覆盖。

---

## 二、已修复：规则表覆盖不到的问法（判成 CHAT + 建议转人工）

这一组共 8 条，形态一致：规则没认出来的问法，在没有 LLM 时不只是答不好，
还会顺带建议转人工（`source: default`、`confidence: 0.4`，低于
`intent_handoff_confidence`，`next_action` 变 `HANDOFF_SUGGESTED`）。

| case | 输入 | 曾经的错法 | 修法 |
|---|---|---|---|
| `order-006` | 订单 …CCDD 现在什么状态 | CHAT / HANDOFF_SUGGESTED | `classify_high_confidence_intent` 增加"单号 + 状态/进展/情况/怎么样了"→ `QUERY_ORDER` 分支 |
| `refund-005` | 这东西我不想要了，退了吧 …CCDD_2 | CHAT / HANDOFF_SUGGESTED | `REFUND` 分支补「退了吧 / 退掉 / 想退 / 要退」 |
| `cancel-002` | 这个订单不要了，取消 …CCDD | CHAT / HANDOFF_SUGGESTED | `CANCEL_ORDER` 分支补组合判断「取消」+「订单」都在即命中（howto 分支在前，政策问法不受影响） |
| `review-003` | 给这个订单打3分 一般吧 …CCDD | CHAT / HANDOFF_SUGGESTED | 意图分支补正则 `打\s*\d+\s*分`——原表「打分」匹配不上"打3分"，判定和取参数的 `extract_review_star` 支持度不一致 |
| `review-004` | 我要评价 | CHAT / HANDOFF_SUGGESTED | 补「我要评价 / 想评价 / 去评价 / 评价一下」等裸评价意图，单号由工具层追问 |
| `search-009` | 帮我搜一下无线耳机 | CHAT / HANDOFF_SUGGESTED | 品类表补高频数码/小家电词（耳机、空气炸锅等 13 个） |
| `search-010` | 有便宜点的空气炸锅吗 | CHAT / HANDOFF_SUGGESTED | 同上 |
| `refund-006` | 七天无理由怎么退 | CHAT 但 HANDOFF_SUGGESTED | howto 分支的宾语表补单字「退」（原来只有「退款/退货」） |

> 说明：`search-009` / `search-010` 的修法是补词，**枚举品类这条路本身有上限**
> （`_ALL_PRODUCT_HINTS` 不可能穷尽），补词只是挪动边界。这两条原本就靠 LLM 兜住，
> 离线规则只是把高频品类垫得更厚。上线策略不变：LLM 不可用时的行为可接受即可。

---

## 三、已修复：规则命中顺序不对

| case | 输入 | 曾经的错法 | 成因与修法 |
|---|---|---|---|
| `refund-007` | 退款要多久到账 | REFUND | 进度关键词表只有「退款到账」，「要多久到账」没命中，被后面的「退款」泛匹配抢走。修法：进度表补「多久到账 / 几天到账 / 何时到账 / 什么时候到账 / 多久退 / 几天退」 |
| `logi-006` | 物流一直不动怎么办 | CHAT | 「怎么」+「物流」命中 howto 分支，在物流分支之前返回。修法：物流异常问法（不动/没动静/卡住/停滞/不更新/丢件等）在 howto 之前独立成 `QUERY_LOGISTICS` 分支 |

---

## 四、已修复：`wants_order_list_cards` 和意图分类互相矛盾

| case | 输入 | 曾经的错法 | 成因 |
|---|---|---|---|
| `order-007` | 我上次买的那个还没发货吗 | PRODUCT_SEARCH | `_ORDER_LIST_UI_HINTS` 有「上次买」，但 `looks_like_direct_product_keyword` 的排除表漏了它，被兜底的「买」泛匹配判成商品搜索 |
| `order-009` | 我想再买一次上次那个 | PRODUCT_SEARCH | 同上（「再买一次 / 复购」） |

修法：把「上次买 / 再买一次 / 复购」同时加进 `classify_high_confidence_intent` 与
`classify_intent_by_rules` 的 `QUERY_ORDER` 分支、以及 `looks_like_direct_product_keyword`
和 `looks_like_new_product_search` 的排除表——仓库里不再同时存在"这种问法必须渲染订单卡片"
和"这种问法是商品搜索"两句话。

---

## 五、已修复：`consult-007` 商品页进来但没有咨询卡

| case | 输入 | 曾经的错法 |
|---|---|---|
| `consult-007` | 这款支持几个USB接口（`fromProduct: true`，无卡） | CHAT / HANDOFF_SUGGESTED |

成因：`/sendMessage` 的 `fromProduct` 和 `consultProductId` 是两个独立 Form 字段，
客户端只传 `fromProduct=true` 不传商品 ID 时，快照缺失，`is_product_consult_turn`
在无卡时返回 False，每一句规格追问都落到 CHAT + 建议转人工。

修法：`is_product_consult_turn` 在 `fromProduct=True` 且无卡时，按
`looks_like_consult_followup` 判咨询（类别切换/新搜索已在前面排除）。
顺带补了一个守卫：`HUMAN_HINTS`（转人工等）在任何咨询分支之前拦截——
用户在咨询中要求转人工不能被 PRODUCT_CONSULT 吃掉。

---

## 六、明确不接受"已知失败"的维度

`tests/test_convo_eval_frozen.py::test_security_dimensions_have_no_known_failures`
把这四维钉在 100%，一旦有失败就直接红，不允许写进 `knownFailures`：

| 维度 | 覆盖 | 现状 |
|---|---|---|
| `argsUserId` | 落到 MCP 的 userId 必须是认证得到的调用者 | 5/5 |
| `noSnakeCaseLeak` | `user_id` 写法必须被清掉，不能留给 `_to_mcp_args` 兜回去 | 5/5 |
| `mismatchFlagged` | 模型自称他人身份时必须留下可观测信号 | 5/5 |
| `blocked` | 注入拦截 + 不误伤正常客服问法 | 14/14 |

意图判错是质量问题，可以先记下来慢慢修；身份归属和注入判错是安全问题，没有"先记着"这个档。

`blocked` 那 14 条里有 7 条是**反向**用例（`inject-008` ~ `inject-014`：改需求、
问券的限制、解除账号限制、忘记密码、粘贴含 `System:` 的商品参数……），
要求判定为不拦截。只测"能拦住攻击"的防护会一路收紧到把正常对话也拦掉，
所以这两个方向必须一起测。

---

## 七、已修复（曾经的已知失败）

这 22 条已从 `knownFailures` 移出，题面未改（SHA-256 不变）。留在这里是因为
"修好了"和"标签被改成实现的输出"在 diff 上很像，需要有地方说清是哪一种。

### 7.1 意图路由（90 → 96）

| case | 输入 | 曾经的错法 | 成因 |
|---|---|---|---|
| `cancel-001` | 帮我取消订单 …CCDD | nextAction ANSWER，应为 TOOL | `CANCEL_ORDER` 不在 `_TOOL_INTENTS` 里，但 `required_tool_for_intent` 又强制映射到 `QUERY_ORDERS`——一处说不用工具，另一处说必须调 |
| `cancel-003` | 取消订单 | 同上 | 同上 |
| `chat-005` | 发票怎么申请 | 判成 INVOICE | `发票` 分支排在 howto 分支之前，问方法被当成要开票 |
| `receipt-004` | 确认收货在哪里点 | 判成 CONFIRM_RECEIPT | howto 关键词表有 `在哪用/在哪看`，没有 `在哪里点` |
| `receipt-005` | 确认收货在哪里点 …CCDD | CONFIRM_RECEIPT + 真的产出确认卡 | 同上，但带了单号，于是走到 `PROPOSE_CONFIRM_RECEIPT` |
| `coupon-005` | 这张券为什么用不了 | CHAT / HANDOFF_SUGGESTED | 分支要求完整的 `优惠券`/`优惠卷`，用户说的是"这张券" |

### 7.2 资金争议（96 → 99）

| case | 输入 | 曾经的错法 | 成因 |
|---|---|---|---|
| `after-004` | 支付失败了但是钱扣了 | PAYMENT_ISSUE / ANSWER，不转人工 | 风险表有 `扣了钱` 没有 `钱扣了`，词序一换就漏 |
| `after-005` | 同一笔订单重复支付了两次 | 同上 | `重复支付` 只被意图表收了，风险表没有 |
| `cancel-005` | 订单已经取消了为什么还扣款 | CHAT / LOW_CONFIDENCE | 两张表都只有 `扣款了`，连意图都没判出来 |

修法是把支付词汇收成一张分级的表（`app/domain/intent/classifier.py`）：

```
FUND_AT_RISK      钱已经动了/该退没退  → RiskLevel.HIGH → FUND_DISPUTE 转人工
PAYMENT_BLOCKED   支付走不通但钱没动    → 仍是 PAYMENT_ISSUE，但不必然转人工
PAYMENT_ISSUE_HINTS = FUND_AT_RISK + PAYMENT_BLOCKED   ← 意图分支读这个
```

`tests/test_intent_classifier.py` 里两条断言守住它：`FUND_AT_RISK ⊆ PAYMENT_ISSUE_HINTS`
（改回手写清单就红），以及按 `FUND_AT_RISK` 参数化逐词验证能独立触发 `FUND_DISPUTE`
（往表里加词的人不需要记得回来加用例）。

### 7.3 两处说法不一致与关键词覆盖（99 → 112，2026-08-03）

第二节到第五节的 13 条，全部是"两处规则对同一种问法的说法不一致"或"关键词覆盖不全"。
修法不是只补词，是把同一件事收敛到一处：

- 订单历史问法：`_ORDER_LIST_UI_HINTS`、`classify_*` 的 `QUERY_ORDER` 分支、排除表三处对齐；
- 退款进度问法：进度表补时间词，不再被 `退款` 泛匹配抢走；
- 物流异常问法：在 howto 之前独立分支；
- 评价意图：「打3分」用正则、裸「我要评价」直接认；
- 取消意图：「取消」+「订单」组合判断；
- 商品页无卡咨询：`from_product=True` 时按追问判咨询，转人工永远优先。

---

## 八、怎么改这份文档

- 修好一条：从 `aishop_convo_v1.lock.json` 的 `knownFailures` 里删掉，
  同时删掉这里对应的行，重跑 `--bootstrap-lock`。留着不删会被
  `test_known_failures_are_exactly_the_current_failures` 抓到。
- 加 case：改 `aishop_convo_v1.jsonl` → 跑 `validate_convo_eval.py` →
  跑 `run_convo_eval.py --bootstrap-lock` → 在这里写清新增的失败是什么。
- **不要**为了让分数变好看去改 `expect*` 字段。要改期望值，先说明为什么原来那个
  期望是错的客服行为——不能因为实现是那样就说期望错了。
