# Regression Preflight 记录

- 日期：`2026-08-25`（Asia/Shanghai）
- Python 环境：`shop`
- 数据 split：`regression`
- 评测 registry validate：`PASS`（`valid=true`）
- live paired evaluation：`NOT_RUN_DEPENDENCIES_NOT_READY`

## 执行命令

```bash
cd AI_Shop-backend/AI_Shop-agent
conda run -n shop python -m evaluation.cli validate
conda run -n shop python -m evaluation.cli preflight --split regression
```

## 结果

`validate` 成功读取并校验 release registry、development/regression dataset lock 和 SHA-256 元数据。

`preflight` 按 fail-closed 规则退出码 `1`，缺少以下运行依赖：

| 检查 | 状态 | 说明 |
|---|---|---|
| `java-gateway` | NOT_READY | `http://127.0.0.1:8081/internal/product/snapshotBatch` 连接失败 |
| `agent-readiness` | NOT_READY | Agent 运行时 readiness 未满足 |
| `product-catalog` | NOT_READY | 无可用商品目录快照/服务响应 |
| `agent-write-fixture-boundary` | NOT_READY | 未建立可回滚的 Agent 写 fixture 边界 |

原始错误摘要：

```text
evaluation failed closed: PreflightError: required preflight checks failed:
['java-gateway', 'agent-readiness', 'product-catalog', 'agent-write-fixture-boundary']
java_internal_http_failed: All connection attempts failed
```

这份记录只说明当前环境不能安全执行 paired evaluation，不代表候选版本质量失败；依赖 ready 后必须重新运行 preflight，并为新 run 生成新的 source fingerprint、observation 和人工审查包。
