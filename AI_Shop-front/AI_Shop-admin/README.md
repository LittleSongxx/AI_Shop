# AI_Shop-admin

> 内容状态：当前有效
>
> 整改基线：`f639599e335b97f6156cc41923d53948bcbf6549`
>
> 最后核验时间：2026-08-06（Asia/Shanghai）
>
> 适用环境：本地开发、CI 构建与运营后台联调；生产参数以部署清单为准

运营后台（Vue），接口经 Gateway `/admin-api`。

## 目录

| 路径 | 说明 |
|------|------|
| `src/views/Layout.vue` | 主框架（侧栏 + 顶栏） |
| `src/assets/base.scss` | 黑白灰 CSS 变量 |
| `src/assets/aishop-admin.scss` | 表格 / 表单 / 卡片 |
| `src/assets/desktop-admin.scss` | 桌面端布局样式 |

## 开发

```bash
npm install
npm run dev
```

开发环境默认代理到 Gateway `http://localhost:8080`（见 `.env.development`）。
