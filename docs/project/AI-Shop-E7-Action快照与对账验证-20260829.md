# E7 Action 快照与对账验证（2026-08-29）

状态：`CONTROLLED_PREPROD_ONLY`；不代表线上 SLO 或无人值守生产能力。

## 本批收口

- Java `/internal/order/agent/actionCapability` 返回稳定的
  `snapshotVersion`、`snapshotEtag`、`snapshotHash` 和受限快照字段。
- 确认提案把 Java 快照证明写入既有 `params_json`（没有新增表或密钥），并在
  远端写操作前重新查询能力。快照版本、ETag 或当前资格变化时，提案以
  `FAILED` 终结且不发起第二次写入。
- Java 原有事务内身份校验、状态条件更新和幂等账本保持不变；网络超时仍只走
  只读 `actionStatus`/领域状态对账，`INCONCLUSIVE` 与 `MANUAL_REVIEW` 不重放写入。
- Confirm/Cancel API 回传权威状态与对账字段；前端确认卡识别数字和状态名，明确
  展示 `INCONCLUSIVE`、`MANUAL_REVIEW` 等非成功终态。

## 可复核检查

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| Agent | `pytest -q tests/test_action_snapshot.py tests/test_evidence_refs.py tests/test_biz_payload_act.py tests/test_pending_action_business_key.py tests/integration/test_pending_confirm.py` | 60 passed |
| Order | `mvn -q -pl AI_Shop-order/app -am -Dtest=OrderAgentInternalControllerTest,AgentActionStatusServiceTest -Dsurefire.failIfNoSpecifiedTests=false test` | passed |
| Web | `npm run test -- --run tests/agent-action-status.test.ts tests/agent-confirm-card.test.ts` | 7 passed |
| Web | `npm run lint -- --no-fix` | passed |

## 限制与下一步

- 快照比对是写入前的短读，最终授权仍由 Java 事务重鉴权；两次读取之间的并发由
  Java 状态条件和幂等账本兜底。
- 本批没有建设持久 chunk event-store、全量故障注入、生产部署或正式 SLO；这些路线
  变化需要重新确认。
- Text2SQL 继续保持 `TEXT2SQL_STATUS: FROZEN`，本批未修改其代码或历史证据包。
