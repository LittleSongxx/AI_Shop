# 项目所有权与 AI Coding 边界

## 作者来源声明

以下是作者对项目来源的公开声明：Simlect 是作者本人早期完成的电商项目，随后演进并更名为 AI-Shop。Git 历史中的 Audreator、song、Song、EdisonChen 和 LittleSongxx 是作者在不同时期使用的账号或别名，不代表引入了第三方项目模板。Git 本身只记录 author/email，不能独立验证现实身份。

项目使用的 Spring、LangGraph、MCP、Vue 等依赖遵循各自许可证；商品图片的来源 URL、本地路径与 SHA256 记录在 [SOURCE_MANIFEST.json](../AI_Shop-backend/data/simlect-assets/SOURCE_MANIFEST.json)。其中的授权字段仍是部署者声明，不等同于独立法务审计。

## 作者负责范围

- Java 交易域：商品、库存、购物车、订单、支付、优惠券、退款、物流、幂等和消息补偿。
- Python Agent：意图、Workflow/Single-Agent、Checkpoint、预算、受控写入和人工接管。
- RAG/MCP：知识发布、混合检索、引用验证、工具契约、身份与权限边界。
- 质量体系：测试、Episode、Bad Case、故障演练、外部黑盒协议和证据声明边界。
- 用户闭环：推荐卡、确认卡、归因、管理端审核和浏览器验收。

## AI Coding

开发过程中使用过 Codex 等 AI 编程工具辅助检索代码、生成局部实现和测试候选。作者负责：

- 需求和架构边界；
- 交易、安全和数据不变量；
- 逐项代码审查、调试和回归；
- 接受、修改或拒绝 AI 建议；
- 所有最终提交与项目陈述的真实性。

本项目不把 AI 生成代码包装为完全手写，也不把框架自带能力包装为业务已经解决的问题。
