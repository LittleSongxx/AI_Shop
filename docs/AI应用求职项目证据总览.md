# AI_Shop AI 应用求职项目证据总览

> 最后核验：2026-08-14（Asia/Shanghai）
>
> 实施基线：Git HEAD `ef9aa0659a9275a99bb74cdb46e87770150dea0a`；实施前已有工作区边界 SHA-256 见 `docs/evidence-manifest.json`
>
> 适用岗位：AI 应用 / Agent 后端、Java + AI 业务后端、AI 全栈 / Product Engineer、RAG / 搜索应用工程
>
> 明确不覆盖：Agent Infra、Kubernetes/服务网格、训练/微调、量化、推理引擎与推理优化算法

这份文档是当前唯一人工证据入口；精确命令、数据锁、结果路径、证据等级和边界以 [evidence-manifest.json](evidence-manifest.json) 为准。普通运行结果位于被 Git 忽略的 `benchmarks/results/`，本轮没有接受任何 baseline。

## 一、结论

作为秋招 AI 应用岗位项目，AI_Shop 当前已经**充分**：它不是单独的聊天 Demo，而是把电商交易底座、RAG、Agent/Workflow 边界、受控工具写操作、评测、安全、隐私、管理端证据和用户端交互连成了可讲述的闭环。尤其适合证明三件事：能把模型放进真实业务状态机、能处理 AI 的不确定性与权限风险、能用评测和集成测试而不是截图证明改动。

但它仍不是生产经历的替代品。Search 已扩展到中文 v2 的 600 商品/240 查询、WANDS 42,994 商品全库/202 查询/32,919 条有效人工判断，并用 45 条真实 `ProductService` 路径做运行时对齐；RAG v4 扩展到 264 条检索和 60 条生成。v4 正式检索与生成均未过质量门禁，暴露后修复只证明已知坏例得到改善，不能冒充新的 fresh 结果。当前最重要的未完成项是重新冻结未见 RAG 集、真实模型 Agent runtime/消融、两位真实标注者盲评、授权真实用户试用、Java 交易包覆盖率、完整 live E2E 与长期性能/成本数据。面试时必须主动说明这些边界。

## 二、证据等级

| 等级 | 含义 | 当前状态 |
|---|---|---|
| E0 | 源码、迁移、测试、工作流可静态复核 | RBAC/HMAC、隐私任务、前端、CI/SBOM 已具备 |
| E1 | 生产决策内核 + 确定性替身的合成运行时评测 | Commerce、安全、Search/RAG contract、消融已实跑 |
| E2 | 本地真实中间件或进程集成 | Java Testcontainers 已实跑 |
| E3 | 配置真实模型/Embedding/Rerank/实时索引 | Search/RAG 已采集；Agent 在线模型未采集 |
| E4 | 经授权进入批次的真实用户 | 未采集 |

## 三、当前可声称结果

| 能力 | 当前实测 | 面试时正确说法 |
|---|---|---|
| Commerce runtime | 27/27，九个子集；工具和参数正确率 1.0，严重安全违规 0 | “执行生产决策代码的确定性合成套件”，不是在线模型准确率 |
| AI 安全集 | 18/18 | 覆盖注入、恶意通道、IDOR、委托身份和 PII 脱敏；不声称覆盖所有攻击 |
| Search/RAG contract | 162/162，公开集与 holdout 分开 | 证明数据锁和查询理解契约；与下方 live 结果分层报告 |
| 第一轮 Search live（历史保留） | 45 条相关性 case 全部执行；public/holdout Recall@10 均 1.0，MRR 0.8917/0.7889，NDCG@10 0.9078/0.8265 | 解释项目如何从小集发现 Recall@10 饱和；当前求职数字以下方成熟评测为主 |
| 第一轮 RAG live retrieval（历史保留） | 50 条全部执行；public/holdout Recall@K 0.9167/1.0，MRR 0.9167/0.8846，引用正确性 0.9706/0.95 | 保留 2 条公开 RAG 失败；当前求职数字以下方 v3 为主 |
| 第一轮 RAG live generation（历史保留） | `deepseek-v4-flash` 10/10 执行，自动 8/10、AI 初审 9/10 | 旧运行不覆盖；当前结论以下方 v3 一次性 final 为主 |
| 中文成熟 Search v1（历史） | 300 商品、120 查询；冻结链路 public 60 条 Recall@1/3/5=1/1/1；fresh 40 条 Recall@1/3/5=0.50/1/1、NDCG@5 0.9848 | 数据明确为 `SYNTHETIC`；用于解释多 K 和首轮消融，当前以 v2 为主 |
| Search v2 正式 | 中文 600 商品/240 查询，fresh Recall@3/5=0.8896/0.9969、NDCG@5=0.9753；WANDS 全库 42,994 商品/202 查询/32,919 有效判断，condensed NDCG@10=0.7953、MRR@10=0.9796、bpref=0.3434；真实 ProductService 首次 Recall@10=0.3778 | `FAILED_RETAINED`；中文 challenge no-result=0.80，运行时失配失败不被离线高分掩盖；WANDS 未标注项不当负例 |
| Search v2 修复后运行时 | 45/45；catalog Recall@3/5/10=0.9778、MRR@10=0.9444、NDCG@10=0.9497；availability-adjusted Recall@3/5/10=1.0；Embedding 86/86、Rerank 30/30 | `POST_FIX_RUNTIME_REGRESSION`，`holdoutExposed=true`、`freshEvidence=false`；证明运行时修复命中已知问题，不替代新 holdout |
| RAG retrieval v2（历史保留） | 64 条：public 34、known regression 16、fresh 14；Recall@5 均 1.0，fresh no-answer accuracy 0.75 | 用于解释单阈值和窄标签问题，不覆盖旧运行 |
| RAG generation v2（历史保留） | 24/24 执行；自动与 AI 初审均 16 PASS/8 FAIL | `FAILED_RETAINED`，不覆盖旧运行 |
| RAG retrieval v3（历史保留） | 12 份文档/75 chunk/6 FAQ；144 条；fresh Recall@1/3/5=0.9583/1/1、MRR@10=0.9792、NDCG@5=0.9846 | 当时门禁 `PASSED`；v4 扩题后暴露泛化和标签问题，不覆盖 v3 |
| RAG retrieval v4 正式 | 72 public + 144 regression + 48 fresh，共 264/264；fresh Recall@1/3/5=0.6944/0.8056/0.8056、MRR=0.75、NDCG@5=0.7645、no-answer=1.0、injection=0.9167、canonical correctness/coverage=0.7442/0.8056 | `FAILED_RETAINED`；Provider 完整、缓存命中/失败/fallback 为 0不等于质量通过；两条 frozen label 超出知识事实边界 |
| RAG retrieval v4 暴露后回归 | 0 Provider replay 的 fresh Recall@3/5=0.9444、canonical=1.0/0.9444；7 条 live targeted 中 5/5 修复目标通过、2/2 标签限制安全拒答 | `POST_FIX_OFFLINE_REPLAY` / `POST_FIX_TARGETED_REGRESSION`，`holdoutExposed=true`、`freshEvidence=false`；只能说明已知坏例修复有效 |
| RAG generation v4 正式 | 60/60、0 runtime error/严重安全违规；39/60，known 29/40，claim completeness=0.8406、claim support=0.8804、canonical coverage=0.9783、token=23,125；AI 初审 46/14 | `FAILED_RETAINED`，人工状态 `HUMAN_REVIEW_PENDING`；AI 初审不冒充人审 |
| RAG generation v4 暴露后重评 | 0 Provider rescore 为 49/60，known 35/40，claim completeness=0.9674、claim support=0.9565；live targeted `11 → 4 → 1` 定位并修复候选截断 | 总成功率 0.8167 仍低于 0.85；不是 fresh E3，不拼成新 60 条正式成绩 |
| 消融 | 单 Agent vs 多 Agent 27 个配对 case；多 Agent vs workflow 21 个适用 case；成功率差值均为 0 | 进程隔离和可比性已实现；毫秒级 stub 延迟不用于宣称真实性能提升 |
| Java 领域 IT | MiddlewareIT 7/7；TransactionPersistenceIT 3/3 | Testcontainers 真实 MySQL/Redis/RabbitMQ/ES，验证幂等、CAS、库存恢复；不是生产压测 |
| Java 覆盖率 | common 约 12.89%，order 约 13.39% 行覆盖 | 覆盖率仍低，是当前最明确的工程短板，不能包装成高覆盖 |
| 管理端/用户端 | 管理端 7/7、用户端 33/33；两端 lint/build 通过；Mock Playwright 8/8 | 另有 8 条完整本地服务用例和 2 条视口不适用用例按设计跳过；不声称生产 live E2E |
| SBOM/供应链 | 四份 CycloneDX SBOM 可生成，Dependency Review/weekly scan 已配置 | SBOM 已生成；完整周期漏洞扫描不声称本地已通过 |
| 真实模型 | Search/RAG 已采集 E3；Agent 未采集 | 都是 `SYNTHETIC + local-live`，不能表述为真实用户或线上效果 |
| REAL_USER | 未采集 | 不能虚构任务成功率、FCR、CTR/CVR 或 GMV uplift |

## 四、90 秒项目叙事

> AI_Shop 是一个 Spring Cloud Alibaba + FastAPI/LangGraph 的单商户电商系统。我做的重点不是套一个聊天界面，而是让 AI 在真实订单、库存、优惠券和售后状态上工作，并把不确定性限制在业务可接受范围内。读操作通过受控 MCP/内部接口查询权威事实；写操作采用 propose、用户确认、Java 状态校验和业务幂等。RAG 使用 Exact FAQ、自适应 BM25/向量召回、RRF、Rerank、最小充分证据、逐事实句引用和有界 repair，工具与知识进入模型前做注入检疫和 PII 脱敏。工程上补了五角色 RBAC、HMAC 管理员断言、AI 数据导出/删除和 `aishop-eval/v1`。评测方面，我用真实 Embedding/Rerank 与本地 Elasticsearch 评了中文 600 商品/240 查询、WANDS 42,994 商品全库/202 查询，并对 12 份知识做了 264 条检索和 60 条生成。正式 RAG v4 只有 39/60，暴露后修复重评到 49/60，但我没有把它冒充新 holdout，而是保留正式失败和完整坏例。它是 `SYNTHETIC + local-live` 的求职证据，不是生产流量或真实用户效果。

这段开场之后，优先展开一条垂直故事，不要在 90 秒里堆所有技术名词。

## 五、按岗位调整重心

| 岗位 | 首讲内容 | 备选深挖 |
|---|---|---|
| AI 应用 / Agent 后端 | Workflow/Agent 分界、受控工具、Trace、评测、安全 | 多 Agent 隔离、Prompt/通道污染、失败恢复、成本边界 |
| Java + AI 后端 | 订单/支付/退款状态机、幂等、MQ、AI 与领域服务边界 | Testcontainers 并发、管理员 RBAC、内部委托身份 |
| AI 全栈 | 用户问题、结构化卡片、管理端证据、隐私中心、端到端状态 | 权限可见性、异步任务、错误与空状态、前后端数据契约 |
| RAG / Search | 混合检索、RRF/rerank、引用/拒答、holdout、注入防护 | public/holdout live 指标、重复证据评分、误拒答 badcase 与模型波动 |

## 六、最容易被追问的点

1. **27/27 是否只是写死规则？** 回答生产决策函数与外部依赖替身的边界，打开 Runner 说明每个子集实际调用的服务；承认它是 deterministic synthetic，不冒充模型能力。
2. **为什么需要多 Agent？** 只在职责独立、可并行或需上下文/权限隔离时用；简单任务仍走规则、workflow 或单专家。消融当前证明不退化和进程隔离，尚未证明真实模型质量/成本优势。
3. **RAG/Search 的 Recall/MRR 怎么得到？** 中文 Search v2 标签来自 600 件结构化商品的确定性约束，120 public、80 fresh、40 challenge 分开；WANDS 在 42,994 商品全库上使用 202 条 query 和 32,919 条有效人工判断，报告 condensed/judged 指标而不把未标注项当负例；RAG v4 是 72 public + 144 regression + 48 一次性 fresh，标签来自 12 份已发布知识、canonical fact catalog 和 required claims。Provider 冷调用后锁定候选，离线 replay 的 Provider 调用数为 0。
4. **模型为什么不能直接退款/改库存？** 模型只生成结构化提案，用户确认后 Java 重新做身份、归属、状态、金额与幂等校验；库存建议不自动执行。
5. **Prompt Injection 怎么防？** 输入、RAG、工具输出分通道治理；外部内容按不可信数据处理，工具最小权限、系统信道身份、输出脱敏和合成安全集共同约束，不能回答成“靠 system prompt”。
6. **管理员内部接口为什么不只用 Token？** 共享 Token 只能证明服务身份，不能证明具体管理员和权限；HMAC 覆盖请求体、角色、权限、时间戳和 nonce，并支持轮换与防重放。
7. **隐私删除如何处理订单/支付？** AI 消息、摘要、画像、记忆和 Trace 可删除；法律/业务必须保留的数据解除 AI 关联并匿名化，清空聊天与彻底删除 AI 数据是两个动作。
8. **指标如何定义？** verified success 只来自验证器或用户确认；FCR 为成功且 24 小时无转人工/同问题重开；点击/加购 24 小时，支付与负向结果 7 天；没有 REAL_USER 就显示未采集。
9. **性能和成本结论是什么？** v4 fresh 检索的 Embedding/Rerank/Expansion P95 为 987.51/522.83/2356.25ms，端到端 P50/P95 为 613.70/2913.93ms；60 条生成端到端 P50/P95 为 1845.26/3449.83ms，TTFT 为 1347.77/2956.63ms，共 23,125 token。Expansion 只有 16 个样本，分位数只描述本地运行；缺可信人民币单价，只能写 `UNPRICED`。
10. **最大的工程短板？** RAG v4 正式检索和生成均未过门禁，正式生成仅 39/60；暴露后 rescore 为 49/60 仍低于 0.85，且不能当 fresh 结论。还缺新未见集、两位真实 reviewer、Agent 在线模型评测、live E2E 和 REAL_USER；Java 交易包行覆盖约 13%。

## 七、面试最值得准备的六个故事

1. 从“模型记得调用工具”到 forced tool + propose/confirm + Java 幂等。
2. 从普通 RAG 到条件式检索、逐条证据门禁、引用和注入检疫。
3. 从伪多 Agent 到 bounded specialist、最小上下文、只读工具和 root-only 写入口。
4. 从自动重试死路到退款人工复核、CAS 审批和断点恢复。
5. 从共享管理员 Token 到数据库 RBAC + HMAC 断言 + nonce 防重放。
6. 从“测试很多”到统一协议、runtime Runner、holdout、消融和证据等级。

每个故事都按“症状 → 错误假设 → 证据 → 根因 → 方案取舍 → 实测 → 边界”准备。当前入口为 [项目问题排查与修复复盘.md](项目问题排查与修复复盘.md) 和 [Search与RAG成熟评测报告.md](Search与RAG成熟评测报告.md)；用户已删除的旧决策/面经文档不恢复、不引用。

## 八、复跑与一致性检查

```bash
python scripts/check_evidence_manifest.py
python scripts/check_evidence_manifest.py --check-local-results
```

本轮 2026-08-14 回归为 Python `1032 passed / 7 skipped / 0 failed`（7 条均为显式要求真实 MySQL 8 的迁移用例），Ruff、24 项 manifest 普通/本地结果检查、四文档一致性和 `git diff --check` 通过；Java common `59/59`、Search `21/21`，integration profile 下 Testcontainers `MiddlewareIT 7/7`，Reactor build 成功。此前 `TransactionPersistenceIT 3/3` 继续作为 E2 历史证据，本轮未重复执行 order 模块。全仓 `ruff format --check` 仍有 234 个历史文件格式债务，未制造无关批量改动。结果目录不入库，CI 上传 artifact；本轮未执行 `--accept-baseline`。
