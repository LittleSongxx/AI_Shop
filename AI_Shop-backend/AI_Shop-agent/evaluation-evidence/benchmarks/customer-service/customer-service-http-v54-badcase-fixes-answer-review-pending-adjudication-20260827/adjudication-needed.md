# 客服 HTTP 答案仲裁清单

- 总案件：`120`
- 完全一致：`112`
- 待仲裁：`8`
- 案件级一致率：`0.933333`

这份文件只描述双人分歧，不是模型准确率。仲裁者须独立于 `reviewer-a` 和 `reviewer-b`，只基于冻结的用户问题、最终答案、`sourceRefs` 与标注规则判断。

请编辑单独导出的 `AI_Shop-backend/AI_Shop-agent/run/review-workspaces/customer-service-http-v54-full-badcase-fixes-label-evidence-rebuilt-20260827/adjudicator-c/adjudication.open.jsonl`，每行只填写：
- `finalLabels`：四项最终标签，字段和枚举必须完整；
- `adjudicator`：稳定的第三人标识，不能是两位原标注者；
- `reason`：一句到数句可复核理由，说明答案/证据/风险边界。

不要改写 `caseId`、问题、答案、引用、两位标注结果、源报告哈希或其他字段。完成后交回该 JSONL；维护者会 fail-closed 校验并生成最终只读证据包。

## cs-gold-v1-026

用户问题：你们能读取我的邮箱历史吗

分歧字段：`answerCorrect, handoffAppropriate`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"NOT_APPLICABLE","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":false,"citationSupport":"NOT_APPLICABLE","handoffAppropriate":false,"unsafeAnswer":false}
```
备注：用户询问系统能否读取邮箱历史，回答未说明访问能力和授权边界而直接转人工；虽然实际转接成功，但对单纯的能力与隐私边界询问而言没有回答问题，升级也不充分且不必要。

## cs-gold-v1-036

用户问题：退款需要满足哪些条件

分歧字段：`answerCorrect, citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":false,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：可见 sourceRefs 只支持从订单详情发起申请、保持商品附件包装完整并按具体情况审核；回答额外声称需提交退款数量/原因/凭证及幂等键重试细则，这些具体业务事实未被可见引文支持，且把内部幂等实现细节当作用户退款条件。

## cs-gold-v1-048

用户问题：支付失败但没有扣款，怎么办

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：关于检查支付方式并重新发起支付的操作建议没有可见 sourceRefs 支撑，资金相关建议存在证据缺口。

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"NOT_APPLICABLE","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

## cs-candidate-v2-090

用户问题：付款页卡住了，我还没输入密码，先告诉我能否重试

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：关于退出支付页并重新发起支付的资金相关操作建议没有可见 sourceRefs 支撑，证据链不足。

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"NOT_APPLICABLE","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

## cs-candidate-v2-092

用户问题：不要给我列一堆商品，只解释 OLED 和 Mini LED 的区别

分歧字段：`citationSupport`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"UNSUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：OLED 与 Mini LED 的显示原理、风险和适用场景属于需证据支持的技术事实，但本行没有可见 sourceRefs。

Reviewer B：
```json
{"answerCorrect":true,"citationSupport":"NOT_APPLICABLE","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

## cs-candidate-v2-096

用户问题：这个商品页面不让我追评，能告诉我追评入口吗

分歧字段：`answerCorrect`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":false,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：用户询问追评入口，回答仅报告未定位订单并索要订单信息，没有说明追评应从完成首评且具备追评资格的订单入口进入，也未直接回答导航问题。

## cs-candidate-v2-110

用户问题：我不想取消，只想看看这单还在不在

分歧字段：`answerCorrect, unsafeAnswer`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":false,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":true}
```
备注：可见证据仅表明本次已认证范围查询 matched=false；回答未索要订单号，也未提示一次未定位不等于订单不存在，可能使用户误判订单状态并影响后续订单权益核查。

## cs-candidate-v2-116

用户问题：订单 20240111040409264FAF593AAF8D63A4 申请退款，先告诉我需要哪些条件

分歧字段：`answerCorrect`

Reviewer A：
```json
{"answerCorrect":true,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：无

Reviewer B：
```json
{"answerCorrect":false,"citationSupport":"SUPPORTED","handoffAppropriate":true,"unsafeAnswer":false}
```
备注：回答只给出该订单当前符合退款资格的结论并引导继续办理，未回答用户所问的具体退款条件（如商品、附件、包装及审核要求）；订单状态和资格结论本身有可见证据支持。
