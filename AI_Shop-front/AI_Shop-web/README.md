# AI_Shop-web（C 端）

> 内容状态：当前有效
>
> 整改基线：`f639599e335b97f6156cc41923d53948bcbf6549`
>
> 最后核验时间：2026-08-06（Asia/Shanghai）
>
> 适用环境：本地开发、Playwright/Vitest 回归和 C 端联调；生产参数以部署清单为准

一键栈会把实际 Gateway 和 Agent 端口注入 Vite。单独运行 `npm run dev` 时，业务 API
默认代理到 `http://localhost:6050`，Agent HTTP/WS 默认直连 `7050`；可用下列变量覆盖。

| 服务 | 默认目标 |
|------|------|
| 业务 API | `http://localhost:6050` |
| Python Agent HTTP/WS | `http://localhost:7050` / `ws://localhost:7050` |

## 开发
```bash
# 1. 启动 Gateway + 各域服务 + Agent
# 2. 本目录
npm install
npm run dev
```

环境变量：
- `VITE_API_PROXY_TARGET=http://localhost:6050`
- `VITE_AGENT_PROXY_TARGET=http://localhost:7050`
- `VITE_WS_PROXY_TARGET=ws://localhost:7050`

如果通过 Gateway 联调，将 `VITE_API_PROXY_TARGET` 和 Agent 目标改成 Gateway 实际地址；
一键脚本会自动注入，不要把动态端口写死在代码里。

## 生产
`.env.production` 使用相对路径 `/api`、`/ws`，由 Nginx 反代到 Gateway（见 `AI_Shop/deploy/nginx.aishop.conf.example`）。

## 前端目录

| 路径 | 说明 |
|------|------|
| `src/views/`、`src/views/pc/` | 移动端与桌面端页面 |
| `src/components/home/` | PC 主屏相关组件 |
| `src/components/layout/` | 顶栏、底栏、搜索与自适应布局 |
| `src/components/agent/`、`src/views/agent/` | AI 助手消息、引用、商品卡与输入区 |
| `src/styles/` | 全局与平台样式 |
| `src/integrations/featureRegistry.ts` | 后端能力开关 |
| `src/api/modules.ts` | 后端接口与推荐点击上报 |
| `tests/e2e/` | 确定性浏览器回归与显式开启的真实全栈回归 |

## 验证

```bash
npm run lint
npx vue-tsc --noEmit
npm run test
npm run test:e2e
npm run build
```

真实全栈 AI 归因用例默认跳过，只有完整本地栈和演示账号可用时显式执行：

```bash
AISHOP_LIVE_E2E=true \
PLAYWRIGHT_BASE_URL=http://127.0.0.1:6001 \
npx playwright test tests/e2e/live-ai.spec.ts --project=mobile
```
