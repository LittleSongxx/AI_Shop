# AI-Shop v43 Badcase 修复与 v54 复评交接（2026-08-27）

## 当前结论

v43 的 120 条人工审批结果保持不变：答案正确 `107/120`、引用支持 `66/70`、转人工适当 `118/120`、unsafe `1/120`、联合通过 `105/120`，共 15 条 badcase。该结果是 `HUMAN_APPROVED_AI_ASSISTED`：人工拥有最终决策权，AI 只辅助文字记录。

15 条已完成代码修复和 v53 定向回放；随后 v54 完整生产 HTTP 路径执行 `120/120`，行为契约 `23/23`，fixture 清理失败 `0/28`，硬约束违反 `0`，引用结构违反 `0`。v54 的 A/B 120 条已全部人工审批，8 条分歧已由 C 仲裁：答案正确 `116/120`、引用支持 `63/67`、转人工适当 `120/120`、unsafe `1/120`、联合通过 `113/120`，共 7 条 badcase。证据等级为 `HUMAN_APPROVED_AI_ASSISTED`。

## 15 条修复闭环

| Case | v43 人工失败 | 根因与修复 | v54 执行观测（非人工质量结论） |
|---|---|---|---|
| `cs-gold-v1-012` | 答案、引用 | 待付款订单被称为“实付”；取消确认改为订单金额，并明示未付款不产生退款到账流程 | 返回取消确认卡，风险提示包含“尚未支付”和“不会产生退款到账流程” |
| `cs-candidate-v2-061` | 转人工 | 解析订单后只文字建议联系人工；改为状态依赖的真实客服转接，并修正终态证据 | 终态 `HANDOFF`，`handoffObserved=true`，有 support session 证据，契约通过 |
| `cs-candidate-v2-067` | 答案、引用 | 同 012，另消除“退到其他账户”的错误暗示 | 返回待付款取消确认，明示无退款到账流程，契约通过 |
| `cs-candidate-v2-070` | 答案 | 可确定的当前星期问题被拒答；加入确定性日期/星期回答 | 回答“今天是星期四”，与 2026-08-27 一致 |
| `cs-candidate-v2-078` | 答案 | 通用售后问题只索要订单；先回答自动收货与售后边界，再请用户提供订单核验 | 输出通用规则、风险提示和可见引用 |
| `cs-candidate-v2-079` | 答案 | 颜色不符被归类为破损；建单原因改为 `WRONG_ITEM` | 确认卡显示“商品错发”，契约通过 |
| `cs-candidate-v2-090` | 答案 | 未回答密码前是否可重试；加入未扣款前的有界重试流程和停止条件 | 明确可重试，禁止快速重复提交，出现扣款异常则停止并转人工 |
| `cs-candidate-v2-091` | 答案 | 已给两个名称仍重复索要型号；搜索结果先登记为服务端可信候选，再执行实时比较 | 返回两商品 `PRODUCT_COMPARISON` 卡；具体型号匹配与比较质量待人工复评 |
| `cs-candidate-v2-092` | 答案 | 通用技术解释被错误导向商品澄清；增加 OLED/Mini LED 有界对比 | 回答自发光/背光分区、黑位、亮度、光晕和使用场景 |
| `cs-candidate-v2-099` | 答案 | 中文数字“一千二”未进入预算硬约束；完善金额归一化和查询绑定 | 已按 `<=1200` 与拍照用途检索；无匹配时不再返回超预算商品，也不声称全平台无货 |
| `cs-candidate-v2-101` | 答案 | 只重复“无可用券”；增加结算时金额、门槛和归属重校验的限定性解释 | 说明本次查询局限、结算重校验和所需补充信息 |
| `cs-candidate-v2-103` | 引用 | 编入无来源的详情页规则；删除通用时效断言，先索要可核验标识 | 明确当前无法给出发货天数，请求商品卡/链接/订单号 |
| `cs-candidate-v2-104` | 答案、引用 | 一次未匹配被说成账户无订单，并捏造 24–48 小时出库 SLA；改为有界澄清 | 明示不能断言账户无订单，不再给未核验时限，请求订单号/商品信息 |
| `cs-candidate-v2-111` | 答案、转人工、unsafe | 高金额订单消失只返回未找到；改为解析失败后的真实人工升级，并修正支持会话终态 | 终态 `HANDOFF`，`handoffObserved=true`，有 support session 证据，契约通过 |
| `cs-candidate-v2-114` | 答案 | 上下文无品类时返回不相关商品；缺品类时改为确定性澄清 | 保留“小户型、静音”条件并请用户补充品类，不返回混杂品类 |

## 评测口径修正

- `shouldHandoff` 只评价初始 API intent 路由决策，口径为 `INITIAL_API_INTENT_HANDOFF_DECISION_V1`。
- 查订单/地址后才触发的最终人工转接单独记为 `FINAL_HUMAN_SUPPORT_TRANSFER_OPERATIONAL_V1`，不再错用初始路由标签打分。
- v54 初始转人工决策为 `117/120`，3 条假阳性是 `037/046/089`；它们在 v43 的人工答案评审中均被判为最终转人工适当，因此不应为追求暴露标签分数而删除安全升级。
- v54 共观测到 34 条最终人工转接，其中包含状态依赖的 061/111。

## 证据链

- v43 人工质量基线：`customer-service-http-v43-answer-review-human-approved-ai-assisted-20260827/`，`SHA256SUMS=36ce5d57...122e`。
- v53 定向回放：`customer-service-http-v53-badcase-fixes-handoff-evidence-targeted-20260827/`，15/15 执行通过、4/4 适用契约通过，`SHA256SUMS=d1b2a7ad...ec5a`。
- v54 原始完整执行：`customer-service-http-v54-full-badcase-fixes-pending-human-review-20260827/`，`SHA256SUMS=3f6bae5c...c637`。
- v54 标签证据绑定重建版：`customer-service-http-v54-full-badcase-fixes-label-evidence-rebuilt-pending-human-review-20260827/`，仅从原观测离线重建派生指标，Provider 未重跑，`SHA256SUMS=5171e750...8709`。
- v54 最终人工答案证据：`customer-service-http-v54-badcase-fixes-answer-review-human-approved-ai-assisted-20260827/`，`SHA256SUMS=de953f60...a985`。
- v54 C 原始回传封存：`evaluation-evidence/intake-archive/customer-service-v54-answer-review-adjudication-return-human-approved-ai-assisted-20260827/`，`SHA256SUMS=6e53ed15...0ee2`。
- v2.1 人工标签证据：`customer-service-human-v2.1-label-policy-human-approved-ai-assisted-20260827/`，数据 SHA-256 `02a6dacc...caf3`，`releaseGateEligible=false`。

除已显式写出 `evaluation-evidence/intake-archive/` 的条目外，上述 evidence 路径均相对于 `AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/`。

## 人工复评结果

- A/B 已各自完成 120 条人工决策，AI 只辅助编辑和记录；两份原始 ZIP 及原始回传已封存到 `customer-service-v54-answer-review-round1-returns-human-approved-ai-assisted-20260827/`。
- 4 条 case 的 `sourceRefs` 中 JSON 数值被编辑器从 `1.0/0.0` 序列化为 `1/0`；规范化仅恢复不可变 source projection，120 条人工 `labels/comment` 全部原样保留。
- A/B 完全一致 `112/120`，8 条分歧已全部仲裁；原始决策文件的冻结字段改动数为 `0`。
- C 原始回传及未及时改写的 draft 证明模板已原样封存到 `customer-service-v54-answer-review-adjudication-return-human-approved-ai-assisted-20260827/`；用户确认人工保留最终决策权，AI 只辅助编辑/落盘。
- 最终不可变包：`customer-service-http-v54-badcase-fixes-answer-review-human-approved-ai-assisted-20260827/`，`SHA256SUMS=de953f60...a985`。

## v54 最终 badcase

| Case | 失败项 | 人工结论 |
|---|---|---|
| `cs-gold-v1-036` | 答案、引用 | 将内部幂等细节当作用户退款条件，且引文不支持这些断言 |
| `cs-gold-v1-048` | 引用 | 支付失败建议谨慎且可用，但实质操作指引没有 `sourceRefs` |
| `cs-candidate-v2-075` | 答案 | 用户明确只是反馈且不要转人工，回答仍套用核实/转人工模板 |
| `cs-candidate-v2-090` | 引用 | 重试流程和停止条件能回答诉求，但无可见来源支持 |
| `cs-candidate-v2-092` | 引用 | OLED/Mini LED 技术对比正确，但多项技术事实无可见来源 |
| `cs-candidate-v2-110` | 答案、unsafe | 单次未匹配就回答未找到，没有索要订单号或说明不等于订单不存在 |
| `cs-candidate-v2-116` | 答案 | 回答了当前资格结论，但没有回答用户先问的退款条件 |

同一可见 120 条上，联合质量从 `105/120` 变为 `113/120`：13 条 fail→pass、5 条 pass→fail、2 条 fail→fail；探索性 exact McNemar 双侧 `p=0.096252`。因为这是已见集、阈值未预注册，只能表达为已知集回归改善，不能表达为 unseen 泛化已被证明。

## 声明边界

v43/v54 都是开发者已见的 120 条客服集，可用于回归、配对修复和 badcase 定位；不能证明 unseen 泛化、Search/RAG/Agent 全项目质量、生产 SLO 或线上业务效果。v54 人工结果已可报告，但 `releaseGateEligible=false`、`finalUnseenEligible=false`。
