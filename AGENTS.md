# AGENTS.md

本仓库有两部分：

- **v0.1 产品**：Go 操作内核 `kernel/`、`kernel/pg/`，采购领域模块 `internal/procurement/`，入口 `cmd/product`、`cmd/appctl`，界面 `apps/web/app/procurement`。新功能写在这里。
- **历史 POC 与实验**：`benchmark/`、`research/`、`poc/`、`docs/evidence/`、`internal/opkernel`、`internal/opdef`、`internal/policy`、`cmd/server`、`db/`、`policies/`、`generated/`。只读。不要修改，也不要让新代码依赖它们。

设计与边界见 [docs/kernel.md](docs/kernel.md)；本地运行见 [docs/getting-started.md](docs/getting-started.md#运行-v01-产品cmdproduct)。

## 新增或修改一个业务操作

1. 在 `internal/procurement/` 实现 `pg.Operation`：
   - `Definition`：`Ref{Name, Version}`、`MaxTargets`，以及 `Events`（每个事件都要有消费者）或 `NoEventReason`。
   - `Normalize`：严格解析请求（用 `strict`），不访问数据库。请求必须是 JSON 对象，目标放在一个顶层字段里（单个 id 的字符串或 id 数组）。
   - `Authorize`：只看 `kernel.Principal` 的角色。
   - `Execute`：只通过传入的 `*pg.Session` 读写；先用 `lockPurchases` 锁住目标父单，再查状态和金额；任一目标不合格就返回 `deny()`，不写任何数据；如实返回实际改动的目标、每个目标一条审计、以及声明过的事件。
2. 加入 `Registry()`。改动已发布操作的语义（请求字段、校验、规则、事件）时，发布新的 `Version`，不要原地修改。
3. 需要事件时实现 `pg.Consumer`，在 `Handle` 中严格校验载荷，写入投影表时以 `event_id` 去重。
4. 数据库变化只能新增 `internal/procurement/migrations/NNNN_name.up.sql`，编号接在最后一个之后。已发布的 migration 不可修改。新表必须 `FORCE ROW LEVEL SECURITY`，加 `RESTRICTIVE` 租户策略，并且只授予需要的列权限。
5. 运行 `go build -o bin/appctl ./cmd/appctl && bin/appctl describe > internal/procurement/contract.json`。
6. 在 `internal/procurement/*_test.go` 补测试：正向用例、每种拒绝情况零写入、幂等重放。

## 内核与数据库已经保证的内容（不要在领域代码里重做）

事务、幂等与请求指纹（覆盖请求的每个顶层字段）、统一的拒绝消息、按 ID 比对完整目标集合、审计/outbox/结果账本写入、事件与消费者一一对应、租户上下文。数据库触发器保证：状态只能 submitted → approved → closed；审批人不能是创建人；验收合计覆盖金额才能关闭；验收只能记在 approved 的采购单上。新规则如果是跨写路径都必须成立的不变量，也写成触发器或约束。

## 禁止

- 修改 `kernel/`，除非任务明确要求。
- 在 `internal/procurement` 之外写 `procurement.*` 或 `kernel.*` 表；在领域代码中写 `kernel.*` 表、开连接或开事务（`pgxpool`、`database/sql`、`Begin`）。`appctl validate` 会检查这些。
- 修改历史目录、已发布的 migration、`contract.json` 里已发布的版本定义。
- 直接 push main。

## 完成前必须通过

```bash
export KERNEL_TEST_ADMIN_URL='postgres://postgres:postgres@127.0.0.1:5432/postgres?sslmode=disable' KERNEL_REQUIRE_DB=1
go vet ./... && go test -race -count=1 ./kernel/... ./internal/procurement/ ./cmd/appctl/
go build -o bin/appctl ./cmd/appctl && bin/appctl validate --base origin/main
```

`validate` 的输出必须是 `"ok":true`。改了界面时还要运行 `scripts/product_browser.sh`。
