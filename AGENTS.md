# AGENTS.md

本仓库有两部分：

- **v0.1 产品**：Go 操作内核 `kernel/`、`kernel/pg/`，采购领域模块 `internal/procurement/`，入口 `cmd/product`、`cmd/appctl`，界面 `apps/web/app/procurement`。新功能写在这里。
- **历史 POC 与实验**：`benchmark/`、`research/`、`poc/`、`docs/evidence/`、`internal/opkernel`、`internal/opdef`、`internal/policy`、`cmd/server`、`db/`、`policies/`、`generated/`。只读。不要修改，也不要让新代码依赖它们。

设计与边界见 [docs/kernel.md](docs/kernel.md)；本地运行见 [docs/getting-started.md](docs/getting-started.md#运行-v01-产品cmdproduct)。

## 新增或修改一个业务操作

1. 在 `internal/procurement/` 实现 `pg.Operation`：
   - `Definition`：`Ref{Name, Version}`、`Targets`（存放目标的顶层请求字段名，单个 id 用字符串，多个用数组）、`MaxTargets`，以及 `Events`（每个事件都要有消费者）或 `NoEventReason`。
   - `Normalize`：用 `strict` 严格解析请求，不访问数据库。返回的目标必须与 `Targets` 字段的值一致，否则内核返回 Internal。
   - `Authorize`：只看 `kernel.Principal` 的角色。
   - `Execute`：只通过传入的 `*pg.Session` 读写；先用 `lockPurchases` 锁住目标父单，再查状态和金额；任一目标不合格就返回 `deny()`，不写任何数据；如实返回实际改动的目标、每个目标一条审计、以及声明过的事件。
2. 加入 `Registry()`。改动已发布操作的语义（请求字段、校验、规则、事件）时，发布新的 `Version`，不要原地修改。这条也适用于你为了新功能而顺带改动的**其他**操作或事件：例如改变 close 判断"验收是否覆盖金额"的算法，或改变事件载荷字段的含义，都要给那个操作或事件升版本。
   - 事件载荷允许的取值变了，也算含义变化。例如 closed 事件的 `received` 以前一定不小于 `amount`，现在可以更小，就要发布新的事件版本；消费者要继续处理旧版本，因为队列里可能还有旧事件。
   - 数值上下限只按需求写。不要从相邻操作复制 `1e12` 之类的上限；需要上限时，同时在数据库加 `CHECK`，让直接 SQL 也受同样限制。
3. 需要事件时实现 `pg.Consumer`，在 `Handle` 中严格校验载荷，写入投影表时以 `event_id` 去重。消费者处理的每个事件版本都要有 `internal/procurement/events/<consumer>_<type>@<version>.json`（格式参照已有文件），写明消费者必须接受哪些载荷、投影出什么，以及必须拒绝哪些载荷。已发布的事件契约不能修改或删除。载荷的含义或允许的取值要变，就发布新的事件版本，并让消费者同时继续处理旧版本。
4. `Normalize` 里的规范化和校验（去空白后非空、长度、取值范围），只要对应字段能通过你新增的 UPDATE 授权写入，就要在数据库里用 `CHECK` 或触发器做同样的检查，这样直接 SQL 也写不进去。
5. 数据库变化只能新增 `internal/procurement/migrations/NNNN_name.up.sql`，编号接在最后一个之后。已发布的 migration 不可修改。新表必须 `FORCE ROW LEVEL SECURITY`，加 `RESTRICTIVE` 租户策略，并且只授予需要的列权限。
6. 运行 `go build -o bin/appctl ./cmd/appctl && bin/appctl describe > internal/procurement/contract.json`。
   新操作版本还要新增 `internal/procurement/behavior/<name>@<version>.json`（格式参照已有文件），写入这个版本的冻结行为用例：正向、每种拒绝、数值边界，以及结果、状态、审计、事件载荷。已发布的 behavior 文件不能修改；改动后如果任何已发布版本的用例失败，说明它的行为变了，要发布新版本，而不是改用例。
7. 在 `internal/procurement/*_test.go` 补测试：正向用例、每种拒绝情况零写入、幂等重放，以及需求中每个数值边界（刚好允许、刚好超出）。
8. 新增状态、通知类型或改变规则时，同步更新 `docs/kernel.md` 的操作表与"数据库不变量"段落，以及 `apps/web/app/procurement/page.tsx` 中的状态和通知文案（`statusLabel`、通知列表）；新操作要在界面上可用时，还要加入 `apps/web/app/api/product/[...path]/route.ts` 的 `operations` 允许列表。

## 内核与数据库已经保证的内容（不要在领域代码里重做）

事务、幂等与请求指纹（覆盖原始请求的全部字段，只有 `Targets` 字段按集合比较；重复键或只差大小写的键会被拒绝）、统一的拒绝消息、按 ID 比对完整目标集合、审计/outbox/结果账本写入、事件与消费者一一对应、租户上下文。数据库触发器保证：状态只能 submitted → approved → closed；审批人不能是创建人；验收合计覆盖金额才能关闭；验收只能记在 approved 的采购单上。新规则如果是跨写路径都必须成立的不变量，也写成触发器或约束。

## 禁止

- 修改 `kernel/`，除非任务明确要求。
- 在 `internal/procurement` 之外写 `procurement.*` 或 `kernel.*` 表；在领域代码中写 `kernel.*` 表、开连接或开事务（`pgxpool`、`database/sql`、`Begin`），或改动测试运行环境（`testing`、`flag`、`os.Args`、`os.Chdir`、`os.Setenv`、`os.Exit`）。`appctl validate` 静态检查这些（只看字符串字面量；SQL 不要在运行时拼接表名）。
- 修改历史目录、已发布的 migration、`contract.json` 里已发布的版本定义、已发布的 `behavior/*.json` 或 `events/*.json`。
- 直接 push main。

## 完成前必须通过

```bash
export KERNEL_TEST_ADMIN_URL='postgres://postgres:postgres@127.0.0.1:5432/postgres?sslmode=disable' KERNEL_REQUIRE_DB=1
go vet ./... && go test -race -count=1 ./kernel/... ./internal/procurement/... ./cmd/appctl/
python3 scripts/run_frozen_behavior.py
make build && bin/appctl validate --base origin/main
```

`validate` 的输出必须是 `"ok":true`。`make build` 会构建 `bin/appctl` 和 `bin/product`，下面两个脚本用的就是这两个二进制。新增 migration 或改动 `internal/procurement` 时，还要运行 `scripts/product_smoke.sh`。改了界面时，先运行 `make web-deps`（安装依赖、构建界面并安装 Chromium），再运行 `scripts/product_browser.sh`。两个脚本都需要上面的 `KERNEL_TEST_ADMIN_URL`。
