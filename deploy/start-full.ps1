# AI_Shop 完整版：中间件全开 + 打印微服务启动清单
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\deploy\start-full.ps1

$ErrorActionPreference = "Stop"
$Deploy = $PSScriptRoot

Write-Host "======== 1/2 中间件（全开）========" -ForegroundColor Cyan
& (Join-Path $Deploy "start-middleware.ps1")

Write-Host ""
Write-Host "======== 2/2 微服务请在 IDE / 另开终端启动（完整版 10 个）========" -ForegroundColor Cyan
Write-Host "推荐机器：16G+；当前按 32G 本机舒适档配置中间件，可放心全开。"
Write-Host ""
Write-Host "顺序 | 入口类                    | 模块路径                         | 端口 | JAVA_TOOL_OPTIONS"
Write-Host "-----|---------------------------|----------------------------------|------|---------------------------"
Write-Host "  1  | GatewayApplication        | AI_Shop-gateway                  | 8080 | -Xms128m -Xmx256m"
Write-Host "  2  | UserApplication           | AI_Shop-user/app                 | 8105 | -Xms128m -Xmx320m"
Write-Host "  3  | ProductApplication        | AI_Shop-product/app              | 8099 | -Xms128m -Xmx320m"
Write-Host "  4  | StockApplication          | AI_Shop-stock/app                | 8102 | -Xms128m -Xmx256m"
Write-Host "  5  | CartApplication           | AI_Shop-cart/app                 | 8084 | -Xms128m -Xmx256m"
Write-Host "  6  | CouponApplication         | AI_Shop-coupon/app               | 8087 | -Xms128m -Xmx256m"
Write-Host "  7  | OrderApplication          | AI_Shop-order/app                | 8093 | -Xms128m -Xmx384m"
Write-Host "  8  | PayApplication            | AI_Shop-pay/app                  | 8096 | -Xms128m -Xmx256m"
Write-Host "  9  | SearchApplication         | AI_Shop-search                   | 8108 | -Xms128m -Xmx384m"
Write-Host " 10  | AdminApplication          | AI_Shop-admin                    | 8111 | -Xms128m -Xmx256m"
Write-Host ""
Write-Host "前端: cd AI_Shop-front\AI_Shop-web ; npm run dev"
Write-Host "详情: $Deploy\完整环境启动指南.md"
Write-Host ""
Write-Host "不要启动: 各服务 api/ 子模块、AI_Shop-common"
