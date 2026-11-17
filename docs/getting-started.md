# 本地运行与验证

本指南用于运行采购审批示例。研究队列、历史结果和接手任务见 [HANDOFF](HANDOFF.md)；业务内核的独立服务见 [事务化 Action](transactional-action.md)。

## 环境

- Go 1.25+、Node.js 24、Python 3.12+。
- PostgreSQL 17+；以下使用与 CI 一致的 PostgreSQL 17.6。
- 浏览器验收另需 Playwright Chromium。

数据库初始化会删除并重建测试表，只能使用可销毁的 `_poc` 数据库。三条路径共用端口与测试表，按顺序运行。

## 准备数据库

在一个终端启动 PostgreSQL，看到 ready to accept connections 后继续：

```bash
docker run --rm --name enterprise-agent-poc \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=enterprise_agent_poc \
  -p 127.0.0.1:5432:5432 postgres:17.6
```

另一终端进入仓库根目录：

```bash
export ADMIN_DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/enterprise_agent_poc
export DATABASE_URL=postgres://app_runtime:poc-only@127.0.0.1:5432/enterprise_agent_poc
export POC_DB_RESET=1
make deps build
```

这些口令仅用于测试。`make deps` 安装 Node 依赖并生成、检查 ZenStack Client；`make build` 构建 `bin/server`、`bin/appctl` 和 `bin/product`。

## 启动后端

默认体验 Go + PostgreSQL RLS：

```bash
node scripts/db.mjs reset guarded
bin/server --mode guarded
```

其他路径使用同一套数据库环境，但应先停止当前后端，再重置并启动所选路径：

| 路径 | 初始化数据库 | 启动 |
|---|---|---|
| Go 手写授权 | `node scripts/db.mjs reset baseline` | `bin/server --mode baseline` |
| Go + RLS | `node scripts/db.mjs reset guarded` | `bin/server --mode guarded` |
| ZenStack Policy | `node scripts/db.mjs reset zenstack` | `npm start --prefix apps/zenstack` |

后端监听 [127.0.0.1:8080](http://127.0.0.1:8080)。例如，读取 demo 申请人的采购单：

```bash
curl -H 'Authorization: Bearer demo_alice' http://127.0.0.1:8080/purchases
```

这里运行的是原采购 API。新批量 Action 的独立服务及专用 schema 见 [事务化 Action](transactional-action.md)；操作目录内核的能力不等于旧 API/UI 已全部迁移。

## 启动界面

保持所选后端运行，在另一终端进入仓库根目录：

```bash
npm ci --prefix apps/web
npm run build --prefix apps/web
POC_ROUTE=guarded npm start --prefix apps/web
```

打开 [127.0.0.1:3000](http://127.0.0.1:3000)。`POC_ROUTE` 取 `baseline`、`guarded` 或 `zenstack`，应与当前后端一致。界面可切换固定 demo 身份，支持列表、创建和单条审批。

界面以固定上游代理访问后端，不接受浏览器指定任意上游或身份 Header。UI 细节、移动端与自动验收见 [共享 UI](shared-ui.md)。

## 验证

先停止手动启动的后端与界面，保留测试数据库运行。在已导出数据库环境变量的终端，按需执行：

```bash
make test
python3 -m unittest discover -s benchmark/tests -v
make experiment
```

`make test` 执行 Go race test、vet 和基础 Verifier 对抗检查；`make experiment` 顺序运行三路径的业务验收与人工缺陷控制。它们不调用真实模型。

浏览器验证先构建界面、安装 Chromium：

```bash
npm ci --prefix apps/web
npm run build --prefix apps/web
apps/web/node_modules/.bin/playwright install --with-deps chromium
python3 scripts/browser.py --route guarded
```

对其他路径替换 `--route`。结果在 `reports/browser/<route>/`。

完整回归还包括 Action、各阶段评测驱动、归档重放和 oracle 续跑补丁控制。以 [CI 工作流](../.github/workflows/poc.yml) 为完整执行清单；[HANDOFF](HANDOFF.md) 提供对应环境与命令。部分控制拒绝覆盖已有输出，重新执行请使用干净 worktree。`scripts/report.py` 需要完整的 CI 报告集合，不能只跑几个控制后直接生成总报告。

## 验证策略变更

```bash
bin/appctl compile
bin/appctl verify --base origin/main
```

验证范围包括生成漂移、权限扩大和迁移锁；迁移语法采用保守子集，未覆盖的变更需要扩展验证器。候选操作声明与目录检查见 [操作语义设计](operation-semantics.md)。

## 运行 v0.1 产品（cmd/product）

v0.1 采购参考应用独立于上面三条 POC 路线，使用自己的 migrations 和角色。说明见 [发布说明](release-v0.1.md) 与 [内核说明](kernel.md)。

一次性创建角色（集群级，口令仅用于本地）：

```bash
psql "$ADMIN_DATABASE_URL" \
  -c "CREATE ROLE enterprise_runtime NOLOGIN" \
  -c "CREATE ROLE enterprise_consumer NOLOGIN" \
  -c "CREATE ROLE product_app LOGIN PASSWORD 'local-only' NOSUPERUSER NOBYPASSRLS IN ROLE enterprise_runtime" \
  -c "CREATE ROLE product_consumer LOGIN PASSWORD 'local-only' NOSUPERUSER NOBYPASSRLS IN ROLE enterprise_consumer" \
  -c "CREATE DATABASE enterprise_product"
```

迁移、可选的演示数据、启动：

```bash
make build
ADMIN_DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/enterprise_product bin/appctl migrate up
psql postgres://postgres:postgres@127.0.0.1:5432/enterprise_product -f scripts/product_seed.sql   # 可选
DATABASE_URL=postgres://product_app:local-only@127.0.0.1:5432/enterprise_product \
CONSUMER_DATABASE_URL=postgres://product_consumer:local-only@127.0.0.1:5432/enterprise_product \
  bin/product            # 默认 127.0.0.1:8090
```

界面：`npm run build --prefix apps/web && PRODUCT_HTTP_PORT=8090 npm start --prefix apps/web`，打开 <http://127.0.0.1:3000/procurement>。

验证（需要可销毁的 PostgreSQL 超级用户 URL；每个测试自建自删数据库）：

```bash
export KERNEL_TEST_ADMIN_URL='postgres://postgres:postgres@127.0.0.1:5432/postgres?sslmode=disable'
KERNEL_REQUIRE_DB=1 go test -race -count=1 ./kernel/... ./internal/procurement/ ./cmd/appctl/
bin/appctl validate
scripts/product_smoke.sh
scripts/product_browser.sh   # 需先 npm run build 并安装 Playwright Chromium
```

## 端口与输出

- 数据库端口冲突：修改 Docker 映射端口，并同步修改两个数据库 URL。
- 后端端口冲突：在运行后端、UI 或验收前设置 `export POC_HTTP_PORT=18084`，保持各进程一致。
- 先停止旧服务，再切换数据库运行模式；不要让两组控制并发重置同一个数据库。
- 自动验证输出保存在 `reports/`；完整 CI 汇总在 Actions 的 `poc-comparison` artifact。

CI 使用 synthetic、人工控制和已有候选重放，不发起付费模型调用。人工缺陷阻断率和模型首次通过率分别报告；研究数据与限制见 [实验总结](experiment-summary-2026-11-15.md)。
