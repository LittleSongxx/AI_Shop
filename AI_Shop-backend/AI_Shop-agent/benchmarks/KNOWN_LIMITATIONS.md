# Known Limitations — aishop_convo_v1

基线：112 条 case，通过 90（0.803571）。dev 0.838235 / test 0.75。
数据集 SHA-256 `f5859ed69e6e9edd762789f89c5b083cd1c20c2f5a0c93c7ba421bc9a0b6578e`。

下面 22 条是**一次成型跑出来的失败，原样留着**。期望值是按"正确的客服行为应该是什么"写的，
不是按当前实现的输出写的；跑出来不对的没有改成实际输出，也没有重跑取好成绩。
把 label 对齐到实现，任何实现都能得 100 分，那样这个集合就一点用都没有了。

`benchmarks/aishop_convo_v1.lock.json` 里有每条的机器可读记录（失败维度 + 实际输出）。
两边由 `tests/test_convo_eval_frozen.py` 双向对齐：lock 里有的这里必须有，
这里挂着的不能是已经修好的。

下面这个块是给测试读的，必须和 lock 的 `knownFailures` 完全相等。
正文里可以随便引用通过的 case 做对照（比如"`cancel-004` 是通过的"），
所以对照不能靠全文搜 case id，只能靠这一个块。

<!-- KNOWN_FAILURE_IDS:BEGIN -->
```
after-004 after-005 cancel-001 cancel-002 cancel-003 cancel-005
chat-005 consult-007 coupon-005 logi-006 order-006 order-007
order-009 receipt-004 receipt-005 refund-005 refund-006 refund-007
review-003 review-004 search-009 search-010
```
<!-- KNOWN_FAILURE_IDS:END -->

---

## 一、这个评测集没有覆盖什么

1. **不是端到端质量。** 评的是 `resolve_intent(..., allow_llm=False)` 这条确定性路径，
   LLM 那一层完全没评到。线上大部分轮次是 LLM 给意图，规则只在 LLM 之前（structural /
   rule_priority）和之后（rule fallback）起作用。所以这里的 0.803571 **不能**读成
   "线上意图准确率 80%"。
2. **没有真实检索和真实工具结果。** MCP 出网、Java 微服务、Redis 全是桩，
   退款那条链路的订单项数据由题面的 `fixture` 字段喂。所以"参数拼对了"不等于
   "线上这次调用会成功"。
3. **没有多轮。** 每条 case 只有一句用户输入加可选的咨询卡上下文。
   多轮里的指代消解、话题切换、上下文污染都没评。
4. **没有生成质量。** 回答文本好不好、有没有幻觉、有没有把工具结果说错，
   这些一条都没评——这个集合只看"路由到哪、调不调工具、参数是什么"。
5. **规模小。** 112 条，最小的 subset 只有 4 条（`confirm_receipt`）。
   单个 subset 的通过率波动一条 case 就是 20 个点，不适合拿来做细粒度比较。
6. **只有中文。** 规则表全是中文关键词，英文输入的表现除了注入那几条之外没有覆盖。

---

## 二、留着的失败：规则表覆盖不到（判成 CHAT，低置信度顺带建议转人工）

这一组的共同形态是 `source: default`、`confidence: 0.4`，低于 `intent_handoff_confidence`
（0.55），于是 `next_action` 变成 `HANDOFF_SUGGESTED`。也就是说规则没认出来的问法，
在没有 LLM 时不只是答不好，还会顺带建议转人工。

| case | 输入 | 期望 | 实际 |
|---|---|---|---|
| `order-006` | 订单 …CCDD 现在什么状态 | QUERY_ORDER + 带单号查 | CHAT / HANDOFF_SUGGESTED |
| `refund-005` | 这东西我不想要了，退了吧 …CCDD_2 | REFUND + PROPOSE_REFUND | CHAT / HANDOFF_SUGGESTED |
| `cancel-002` | 这个订单不要了，取消 …CCDD | CANCEL_ORDER + QUERY_ORDERS | CHAT / HANDOFF_SUGGESTED |
| `review-003` | 给这个订单打3分 一般吧 …CCDD | PRODUCT_REVIEW + star=3 | CHAT / HANDOFF_SUGGESTED |
| `review-004` | 我要评价 | PRODUCT_REVIEW（再追问单号） | CHAT / HANDOFF_SUGGESTED |
| `coupon-005` | 这张券为什么用不了 | QUERY_COUPON + 查券 | CHAT / HANDOFF_SUGGESTED |
| `search-009` | 帮我搜一下无线耳机 | PRODUCT_SEARCH | CHAT / HANDOFF_SUGGESTED |
| `search-010` | 有便宜点的空气炸锅吗 | PRODUCT_SEARCH | CHAT / HANDOFF_SUGGESTED |
| `refund-006` | 七天无理由怎么退 | CHAT + ANSWER | CHAT 但 HANDOFF_SUGGESTED |

具体原因各不相同，值得分开看：

- **`order-006`**：带了单号但问法是"现在什么状态"。`classify_high_confidence_intent`
  只在文本命中 `我的订单/查订单/最近买…` 这类词时才认订单意图，"单号 + 问状态"不在表里。
  一个用户把单号粘进来问状态是很常见的问法。
- **`review-003`**：`extract_review_star` 能从"打3分"取到 3（已验证），
  但意图那一层没认出来，所以取参数的代码根本没被调用。
  `PRODUCT_REVIEW` 分支要求命中 `评价/好评/差评/打分/评星/星级` 之一——
  "打3分"中间夹了数字，`打分` 匹配不上。判定和取参数两处对同一种写法的支持度不一致。
- **`review-004`**：只说"我要评价"没给单号。理想行为是认出评价意图再追问是哪一单；
  现在是直接落到 CHAT 并建议转人工。
- **`coupon-005`**：这条是这组里业务影响最大的。用户说券用不了，正确做法是先把他手上的券查出来
  再对着门槛解释；现在既不查券也不答，直接建议转人工。原因是分支要求出现完整的
  `优惠券`/`优惠卷`，而用户说的是"这张券"。
- **`search-009` / `search-010`**：品类词靠 `rules.py` 里几张硬编码 hints 表列举
  （`_PHONE_HINTS`、`_SNACK_HINTS`…），"无线耳机""空气炸锅"不在表里。
  这不是"再加两个词"能解决的——枚举品类这条路本身有上限。
  正常情况下 LLM 兜住了这类输入，但 LLM 不可用时这就是行为。
- **`refund-006`**：意图判对了（CHAT），只是走的是 default 分支而不是 howto 分支，
  0.4 的置信度触发了转人工建议。`howto` 分支的宾语表里有 `退款`/`退货` 但没有单字 `退`，
  所以"七天无理由怎么退"没命中。**问规则的问题不该建议转人工**——这类问题恰恰是
  自助能答的。

---

## 三、留着的失败：规则命中顺序不对

| case | 输入 | 期望 | 实际 | 成因 |
|---|---|---|---|---|
| `refund-007` | 退款要多久到账 | REFUND_STATUS | REFUND | 进度关键词表里只有 `退款到账`，"要多久到账"没命中，被后面的 `退款` 泛匹配抢走 |
| `chat-005` | 发票怎么申请 | CHAT | INVOICE | `发票` 分支在 howto 分支之前，问方法被当成要开票 |
| `receipt-004` | 确认收货在哪里点 | CHAT | CONFIRM_RECEIPT | howto 关键词表有 `在哪用/在哪看`，没有 `在哪里点` |
| `receipt-005` | 确认收货在哪里点 …CCDD | CHAT | CONFIRM_RECEIPT + **PROPOSE_CONFIRM_RECEIPT** | 同上，但带了单号 |
| `logi-006` | 物流一直不动怎么办 | QUERY_LOGISTICS | CHAT | `怎么` + `物流` 命中 howto，在物流分支之前返回 |

`receipt-005` 是这一组里唯一会产生副作用的：已验证在带单号时
`required_tool_for_intent` 会真的返回 `('PROPOSE_CONFIRM_RECEIPT', {'orderId': ...})`，
也就是用户问"在哪里点"会收到一张确认收货的确认卡。

被 `PROPOSE_*` + 用户点确认这一层挡住了，不会直接落库（这也是
`app/domain/tool_policy.py` 里"所有写操作都是提案"这个设计在起作用），
但用户仍然会看到一张他没要的确认卡。

注意 `cancel-004`（怎么取消订单）是**通过**的：那条 howto 覆盖到了。
同一类问法在取消上答对、在发票和确认收货上答错——说明这不是"要不要做 howto 识别"的
取舍，而是 howto 这层做了一半。

---

## 四、留着的失败：`wants_order_list_cards` 和意图分类互相矛盾

| case | 输入 | 期望 | 实际 |
|---|---|---|---|
| `order-007` | 我上次买的那个还没发货吗 | QUERY_ORDER | PRODUCT_SEARCH |
| `order-009` | 我想再买一次上次那个 | QUERY_ORDER | PRODUCT_SEARCH |

这两条已验证 `rules.wants_order_list_cards()` 返回 **True**
（`_ORDER_LIST_UI_HINTS` 里明确列着 `上次买`、`再买一次`、`复购`），
但 `classify_intent_by_rules` 判成 `PRODUCT_SEARCH`。

也就是说仓库里同时存在两句话："这种问法必须渲染订单卡片"和"这种问法是商品搜索"。
`looks_like_direct_product_keyword` 里有一张排除表（`买了什么`、`最近买`…），
但漏了 `上次买`。`wants_order_list_cards` 那张表是后来为了统一 UI 约定合并出来的，
排除表没跟着更新。

后果是用户问"上次买的发货了吗"会收到一堆商品推荐。

---

## 五、留着的失败：资金争议没有升级

| case | 输入 | 期望 | 实际 |
|---|---|---|---|
| `after-004` | 支付失败了但是钱扣了 | HANDOFF / FUND_DISPUTE | PAYMENT_ISSUE / ANSWER，无 handoff |
| `after-005` | 同一笔订单重复支付了两次 | HANDOFF / FUND_DISPUTE | PAYMENT_ISSUE / ANSWER，无 handoff |
| `cancel-005` | 订单已经取消了为什么还扣款 | PAYMENT_ISSUE + FUND_DISPUTE | CHAT / LOW_CONFIDENCE |

意图那一层认出了 `PAYMENT_ISSUE`（前两条），但 `_FUND_RISK_HINTS` 没命中，
所以 `risk_level` 停在 MEDIUM，`_apply_handoff_policy` 的 `fund_dispute` 分支不触发。

两张表对同一件事的说法不一致：意图分支收 `扣款了`/`扣了钱`/`重复支付`，
`_FUND_RISK_HINTS` 只收 `重复扣款`/`扣款了`/`扣了钱`。用户说"钱扣了"（词序不同）、
"重复支付了两次"都落在缝里。

`cancel-005` 更靠前一步就丢了：意图都没认出来，靠 0.4 置信度触发的
`LOW_CONFIDENCE` 才勉强转了人工——转是转了，但 urgency 是 HIGH 而不是
资金争议该有的 CRITICAL，`handoff_reason` 也是错的，客服侧看不出这是笔资金问题。

这一组是 22 条里唯一涉及钱的，优先级应当高于其余各条。

---

## 六、留着的失败：意图与 next_action 的定义不一致

| case | 输入 | 期望 | 实际 |
|---|---|---|---|
| `cancel-001` | 帮我取消订单 …CCDD | nextAction TOOL | ANSWER（但 tool 确实是 QUERY_ORDERS） |
| `cancel-003` | 取消订单 | nextAction TOOL | ANSWER |

`CANCEL_ORDER` 不在 `classifier._TOOL_INTENTS` 里，所以 `next_action` 是 `ANSWER`；
但 `write_args.required_tool_for_intent` 里 `CANCEL_ORDER` 明确映射到 `QUERY_ORDERS`。
一处说不用工具，另一处强制调工具。

`cancel-001` 的实际输出里 `tool` 是对的（`QUERY_ORDERS` + 正确单号），只有 `nextAction`
不对，所以线上表现大概率是正常的——但任何依据 `next_action` 做分流的下游
（`agent_queue_service.queue_for_decision`）看到的是"这轮不需要工具"。

---

## 七、`consult-007`：商品页进来但没有咨询卡

| case | 输入 | 期望 | 实际 |
|---|---|---|---|
| `consult-007` | 这款支持几个USB接口（`fromProduct: true`，无卡） | PRODUCT_CONSULT | CHAT / HANDOFF_SUGGESTED |

`is_product_consult_turn` 在没有 `message_card` 也没有 `consult_card` 时返回 False，
`from_product=True` 单独不足以进入咨询分支。

这个输入是真会出现的：`/sendMessage` 的 `fromProduct` 和 `consultProductId` 是两个
独立的 Form 字段（`app/api/routes/agent.py`），客户端只传 `fromProduct=true`
不传商品 ID 时，`agent_service` 只调 `set_consult_active`，不写咨询卡快照。
此后每一句规格追问都会落到 CHAT + 建议转人工。

另外 6 条带卡的咨询 case（`consult-001` ~ `consult-006`）全部通过。

> 说明：这 6 条最初也是失败的，原因是我最早没给它们 `fromProduct: true`——
> 而线上带卡的轮次一定来自商品页。那是题面把线上不会出现的输入喂给了实现，
> 属于我这边的错，已改题面（改的是输入形状，不是期望值）。这里记一句是因为
> "改题面"和"改标签"从 diff 上看很像，需要留下区分。

---

## 八、明确不接受"已知失败"的维度

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

## 九、怎么改这份文档

- 修好一条：从 `aishop_convo_v1.lock.json` 的 `knownFailures` 里删掉，
  同时删掉这里对应的行，重跑 `--bootstrap-lock`。留着不删会被
  `test_known_failures_are_exactly_the_current_failures` 抓到。
- 加 case：改 `aishop_convo_v1.jsonl` → 跑 `validate_convo_eval.py` →
  跑 `run_convo_eval.py --bootstrap-lock` → 在这里写清新增的失败是什么。
- **不要**为了让分数变好看去改 `expect*` 字段。要改期望值，先说明为什么原来那个
  期望是错的客服行为——不能因为实现是那样就说期望错了。
