# AI_Shop 项目收口 Contract 检查

- Schema: `aishop-project-closure-contract/v1`
- Claim: `CURRENT_SOURCE_CONTRACT_OBSERVATION`
- 当前源码 contract cases: `9/9`
- 生产 SLO: `不适用`
- 完整 live paired evaluation: `未运行，依赖未 ready`

| Case | 预期 | 观察 | Contract |
|---|---:|---:|---:|
| `order-status-claim` | True | True | PASS |
| `order-status-without-field-claim` | False | False | PASS |
| `order-product-claim` | True | True | PASS |
| `order-product-mismatch` | False | False | PASS |
| `capability-allowed` | True | True | PASS |
| `capability-polarity-mismatch` | False | False | PASS |
| `refund-versioned-policy` | True | True | PASS |
| `refund-unversioned-policy` | False | False | PASS |
| `selection-item-ownership` | True | True | PASS |

## 限制

- 本报告只验证当前源码中的订单事实、资格和响应校验 contract，不代表线上答案正确率。
- 完整客服 HTTP paired replay 需要 Java、MCP、Worker、MySQL、Redis、ES 和 Provider readiness。
- 未在本报告中把历史 baseline 或旧人工评分迁移到当前候选版本。

## Live 评测入口

`cd AI_Shop-backend/AI_Shop-agent && conda run -n shop python -m evaluation.cli preflight --split regression`
