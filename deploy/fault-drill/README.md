# AI Shop 隔离故障演练

> 内容状态：当前实现，已与源码核验
>
> 整改基线：`f639599e335b97f6156cc41923d53948bcbf6549`
>
> 最后核验时间：2026-08-06（Asia/Shanghai）
> 适用环境：本地 Docker Compose；不得连接演示或生产数据

运行：

```bash
bash deploy/fault-drill/run_fault_drill.sh
```

脚本使用独立的 Compose project、容器、网络、卷、MySQL schema、RabbitMQ
vhost、Redis DB、端口和测试用户。它会真实执行 Worker kill/租约接管、MQ
发布失败恢复、重复投递 fencing，以及 checkpoint Redis 写失败四个场景。

业务负载由 `fault_drill.worker.FaultDrillWorker` 确定性实现，但队列消费、MySQL
任务账本、claim、续租、恢复扫描、重试、终态 fencing 和 Redis 用户锁全部复用
生产 `AgentWorker` 路径。这样可以隔离模型、RAG、MCP 和 WebSocket 的无关故障，
不能把该演练描述成完整线上链路或网络分区下的 exactly-once 证明。

原始证据写入 `run/evidence/<实际时间>/`，包括命令、基线 commit、容器日志、
SQL、RabbitMQ 状态、逐场景 JSON 和断言结果。`run/` 已被 Git 忽略。脚本退出前
先采证，再删除所有仅含本次测试数据的容器、网络和卷。

最近一次真实运行（2026-08-06 12:45:25–12:47:18，基线 commit 同上）结果为
`PASS`：4 个场景、6 条业务断言和 1 条确定性 LLM stub 断言全部通过。原始目录为
`run/evidence/20260806_124525_+0800/`；这条摘要不替代重新运行产生的新证据。
