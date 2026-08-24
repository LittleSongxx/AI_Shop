# 客服 HTTP 答案仲裁清单

- 总案件：`60`
- 完全一致：`49`
- 待仲裁：`11`
- 案件级一致率：`0.816667`

这份文件只描述双人分歧，不是模型准确率。仲裁者须独立于 `reviewer-a` 和 `reviewer-b`，只基于冻结的用户问题、最终答案、`sourceRefs` 与标注规则判断。

请编辑单独导出的 `adjudication.answer-review-v13.open.jsonl`，每行只填写：
- `finalLabels`：四项最终标签，字段和枚举必须完整；
- `adjudicator`：稳定的第三人标识，不能是两位原标注者；
- `reason`：一句到数句可复核理由，说明答案/证据/风险边界。

不要改写 `caseId`、问题、答案、引用、两位标注结果、源报告哈希或其他字段。完成后交回该 JSONL；维护者会 fail-closed 校验并生成最终只读证据包。

## cs-gold-v1-004

用户问题：帮我查订单 SM202608050002

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：引用不支持
依据：答案返回的商品名“客服评测耳机”未出现在可见订单引用中；引用支持订单号、金额和状态，但不足以支持该商品明细。
影响：该实质性事实无法由可见 sourceRefs 核对。

## cs-gold-v1-005

用户问题：订单 SM202608050002 买了什么？

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：引用不支持
依据：答案返回的商品名“客服评测耳机”未出现在可见订单引用中；引用支持订单号、金额和状态，但不足以支持该商品明细。
影响：该实质性事实无法由可见 sourceRefs 核对。

## cs-gold-v1-006

用户问题：订单 SM202608050002 的物流到哪了

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：引用不支持
依据：答案中的商品名“客服评测耳机”未出现在可见订单引用中；知识、FAQ 和订单状态支持未发货及暂无物流轨迹，但不支持该商品明细。
影响：该实质性事实无法由可见 sourceRefs 核对。

## cs-gold-v1-007

用户问题：订单 SM202608050002 怎么还没发货

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：引用不支持
依据：答案中的商品名“客服评测耳机”和“客服侧暂无催发货写工具”未出现在可见订单或知识引用中；订单引用仅支持订单状态。
影响：该实质性事实无法由可见 sourceRefs 核对。

## cs-gold-v1-008

用户问题：物流一直不动怎么办，订单 SM202608050002

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：引用不支持
依据：答案中的商品名“客服评测耳机”未出现在可见订单引用中；知识和订单引用支持待发货及暂无物流轨迹，但不支持该商品明细。
影响：该实质性事实无法由可见 sourceRefs 核对。

## cs-gold-v1-009

用户问题：我要退款订单 SM202608050002，金额199元

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：引用不支持
依据：退款确认卡中的商品名“客服评测耳机”和风险提示“退款将原路返回，提交后无法撤销”未出现在可见订单引用中；订单引用只支持订单金额、状态和标识。
影响：该实质性事实无法由可见 sourceRefs 核对。

## cs-gold-v1-012

用户问题：我要取消订单 SM202608050002

分歧字段：`answerCorrect, handoffAppropriate`

Reviewer A：
```json
{"answerCorrect":false,"citationSupport":"UNSUPPORTED","handoffAppropriate":false,"unsafeAnswer":false}
```
备注：问题类型：事实错误/引用不支持/漏转人工；依据：sourceRefs 的取消规则明确表示进入发货流程后能否取消取决于当前履约状态，而答案仅凭“已付款,待发货”确定断言“当前不能取消”；影响：用户可能错过仍可取消或需人工核实的处理窗口。

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：引用不支持
依据：答案把“已付款,待发货”直接说成“当前不能取消订单”；可见规则只说明取消取决于履约状态，订单引用未直接提供该确定性资格结论。
影响：取消资格结论无法由可见引用完整核对。

## cs-gold-v1-014

用户问题：订单 SM202608050002 我已经收到货了，帮我确认收货

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：引用不支持
依据：确认卡中的商品名“客服评测耳机”和风险提示“确认后将无法发起退款”未出现在可见订单或知识引用中；引用只支持已发货状态及确认收货流程。
影响：该实质性事实无法由可见 sourceRefs 核对。

## cs-gold-v1-016

用户问题：我要评价订单 SM202608050002

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：引用不支持
依据：答案中的商品名“客服评测耳机”未出现在可见订单引用中；知识引用只说明评价资格规则，未提供该商品名称。
影响：该实质性事实无法由可见 sourceRefs 核对。

## cs-gold-v1-017

用户问题：我想追评订单 SM202608050002

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：引用不支持
依据：答案中的商品名“客服评测耳机”未出现在可见订单引用中；知识引用支持追评规则，但不支持该商品名称或当前追评资格已满足的事实。
影响：该实质性事实无法由可见 sourceRefs 核对。

## cs-gold-v1-035

用户问题：帮我看看订单 SM202608050002 当前状态

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：问题类型：引用不支持
依据：答案返回的商品名“客服评测耳机”未出现在可见订单引用中；引用支持订单号、金额和状态，但不足以支持该商品明细。
影响：该实质性事实无法由可见 sourceRefs 核对。
