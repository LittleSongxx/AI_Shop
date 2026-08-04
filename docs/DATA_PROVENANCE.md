# 数据与指标口径

引用仓库里任何数字之前，先分清它属于哪一类。三类之间不可互相替代：

| 类别 | 含义 | 仓库里的例子 |
|---|---|---|
| **实测** | 在本仓库环境下真实跑出来的结果，可复现 | `benchmarks/results/convo_eval_v1_summary.json`（冻结评测基线 120/120，`run_convo_eval.py` 可复现）；pytest 测试计数；CI 构建结果 |
| **框架就绪但没数据** | 监控/评测设施已接好，但没有真实业务流量喂数据 | Prometheus 指标面板（本地无流量时全为零）；Grafana 告警规则；`online-ai-eval.yml` 手工触发的在线评测门禁 |
| **合成或手工编写** | 为演示/联调造的样例数据或手工整理的说明 | `benchmarks/aishop_convo_v1.jsonl` 的题面（合成对话）；`scripts/rag_golden.jsonl`（**含占位 ID，见下方说明**）；README 架构图 |

## 商品目录镜像

本地演示目录 `simlect-mirror-a7d6063f05a397a6` 经项目所有者确认具备重新分发授权，
来源为 `https://www.simlect.com` 的公开商城接口。镜像包含 47 件商品、对应分类与 SKU
元数据，以及 487 张商品图片。

- 目录快照：`AI_Shop-backend/data/simlect_catalog/`
- 幂等数据库种子：`AI_Shop-backend/data/02_simlect_catalog_seed.sql`
- 图片与逐文件来源、大小、SHA-256：`AI_Shop-backend/data/simlect-assets/SOURCE_MANIFEST.json`
- 同步、完整性校验和本地安装工具：`AI_Shop-backend/data/tools/sync_simlect_catalog.py`

全新克隆可以执行 `python AI_Shop-backend/data/tools/sync_simlect_catalog.py --check`
离线核对快照、SQL 和图片；只有显式使用 `--sync` 或 `--refresh` 时才会访问来源站点。

## 必须注意的几处

1. **RAG golden 集的占位 ID**：`scripts/rag_golden.jsonl` 首行写明需用真实知识发布中的 ID 替换占位值。
   在真实索引上重跑 `scripts/eval_rag.py` 之前，任何 Recall/MRR 数字都不能当作线上水平。
2. **冻结评测集不是线上准确率**：`aishop_convo_v1` 评的是 `resolve_intent(..., allow_llm=False)`
   这条确定性路径，没有 LLM、检索和真实工具。120/120 只能读作"离线规则层通过全部题面"，
   不能读成"线上意图准确率 100%"。覆盖边界见 `benchmarks/KNOWN_LIMITATIONS.md` 第一节。
3. **测试数量**：以 CI（`.github/workflows/ci.yml`）的 pytest 输出为准；README 里的数字是
   某个时点的快照，可能滞后。
4. **审计报告**：`docs/AI_AGENT_MATURITY_AUDIT_2026-07-30.md` 是基于代码、配置与公开资料的
   工程审计，评的是机制与设计风险，不等同于真实生产流量下的容量或安全认证。
5. **端口/部署口径**：`deploy/MIDDLEWARE_DOCKER.md` 记录了宿主机端口 +1 偏移的约定，
   配置文件默认值已与 compose 对齐，引用端口前先看那份文档。
