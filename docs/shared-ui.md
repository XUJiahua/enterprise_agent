# 共享 Next.js UI 与浏览器验收

同一份 Next.js 16.4.6 生产构建服务于 baseline / zenstack / guarded。界面支持采购单列表、创建、审批、体验身份切换和两租户隔离；桌面和 390px 移动端均有截图与交互验收。

## 启动

先按 README 配置数据库、构建 Go 和生成 ZenStack Client，然后运行：

```bash
# 选择 guarded；其他路径见 README
node scripts/db.mjs reset guarded
bin/server --mode guarded
```

另一终端：

```bash
npm ci --prefix apps/web
npm run build --prefix apps/web
POC_ROUTE=guarded npm start --prefix apps/web
```

打开 http://127.0.0.1:3000。后端仍在 127.0.0.1:8080；Next.js 代理使用固定目标，浏览器不能指定上游地址，也不能转发 Authorization / tenant / role Header。身份选择只映射到固定 demo Token，不是生产登录。

切换身份会立即清空旧租户记录，取消上一个列表请求，并忽略迟到的旧响应。审批人不能通过按钮审批自己的单，后端仍独立强制授权；隐藏按钮不是权限边界。

## 自动验收

```bash
apps/web/node_modules/.bin/playwright install --with-deps chromium
python3 scripts/browser.py --route guarded
```

脚本重置数据库、启动指定后端，再用 production Next.js 和真实 Chromium 执行同一份六项验收。对另外两条路径重复同一命令即可。不能并发使用同一数据库/端口。

结果在 `reports/browser/<route>/`：JSON、HTML 报告、后端日志、桌面及移动截图，失败时还保留 trace。Public CI 的 `poc-comparison` artifact 会汇总三条路径的全部证据。

本轮不发布公开体验站，UI 和服务均监听 loopback。UI 的币种及金额只用于采购演示，没有实现财务记账语义。
