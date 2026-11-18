# v0.1 操作内核

设计与里程碑见 [issue #36](https://github.com/XUJiahua/enterprise_agent/issues/36)。本文只记录已实现的部分。

## 包与依赖方向

| 包 | 内容 | 依赖 |
|---|---|---|
| `kernel` | `Principal`、`Ref`、`Definition`、`Input`、`Outcome`、错误分类、请求指纹 | 仅标准库 |
| `kernel/pg` | `Runner`（唯一受管写入口）、`Session`、`Schema`（kernel.request/audit/outbox）、`CheckRuntime` | `kernel`、pgx v5 |
| `kernel/pg/pgtest` | 测试用一次性数据库 | pgx v5 |
| `internal/procurement` | `create@1`、`approve@1`、`receive@1`、`close@2`、产品 migrations 与 `Migrate`、HTTP `Handler` | `kernel`、`kernel/pg`、golang-migrate v4.19.1 |
| `examples/external` | 仓库外 module 导入内核的最小示例 | `kernel`、`kernel/pg` |

`kernel/...` 不 import 本仓库任何其他包，`TestKernelImportsNoInternalCode` 检查这一点。

## Runner.Run 执行顺序

1. 查找已注册的 `Ref`；未注册返回 `Invalid`。
2. 无租户或 actor 返回 `Denied`；校验幂等 key 与请求大小。
3. 领域 `Normalize`：严格解析，产生目标与规范化 payload；`kernel.Targets` 排序并拒绝空、超限、重复。
4. 领域 `Authorize`：操作级权限。重放同样要先通过这一步。
5. 开事务，用 `set_config(..., true)` 绑定 `app.tenant/app.actor/app.roles`（事务内有效，连接归还后不残留）。
6. 以 operation+version+tenant+actor+key 取 advisory xact lock，查 `kernel.request`：同指纹返回原结果字节（`Replayed`），不同指纹返回 `Conflict`。
7. 领域 `Execute`：通过 `Session` 锁定、检查、修改，返回 `Outcome`。
8. 校验 `Outcome`：实际目标集合按 ID 等于请求集合；每目标一条审计对象；事件必须在 `Definition.Events` 中、落在请求目标上，且声明了事件时每个目标至少一条。
9. 写 `kernel.request`、`kernel.audit`、`kernel.outbox`，commit。commit 失败返回 `Retryable`（结果未知，用同 key 重试可得知）。

第 7 步之后任何一步失败，业务修改与 kernel 行全部回滚。

内核自己保证、不依赖领域代码写对的约定（v0.1.1）：

- **请求指纹**（`kernel.RequestFingerprint`，前缀 `v2:`）覆盖原始请求 JSON 的全部字段，数字按原文保留；只有 `Definition.Targets` 声明的字段按集合比较，其他数组保留顺序。任意层级出现重复键或只差大小写的键，返回 Invalid，因此指纹和 Normalize 不会读到不同的值。即使 Normalize 漏掉某个字段，不同请求也不会被当成同一请求重放。Normalize 返回的目标与声明字段不一致时，返回 Internal。v0.1 写入的无前缀账本行仍按旧算法比较，可以继续重放（`TestV01LedgerRowsReplay`）。
- **统一拒绝**：Authorize 或 Execute 返回的任何 Denied 错误，对客户端的消息都被替换为 `kernel.UniformDenial`；原错误只保留在 `Err` 中，供日志使用。

## 数据库兜底

- kernel 与 procurement 表都 `FORCE ROW LEVEL SECURITY`，租户条件是 `RESTRICTIVE` policy，后加的 permissive policy 无法放宽。
- runtime 角色只有 `SELECT, INSERT`（kernel 表）和列级 `SELECT`/`UPDATE (status)`（采购表）；`internal_note` 不可读。
- `pg.New` 和 `pg.CheckRuntime` 拒绝 superuser、BYPASSRLS、表 owner 以及未强制 RLS 的表。
- `Session` 不暴露 pool、Begin、Commit、Rollback。这防止误用，不是对同进程 Go 代码的沙箱；直接写 SQL 的代码不会自动获得操作级约束，只受上面的数据库兜底约束。

## 采购纵切（M1b）

| 操作 | 角色 | 规则 | 事件 |
|---|---|---|---|
| `procurement.create@1` | requester | 新建 submitted 采购单（客户端给定 id，同租户 id 已占用则 Denied） | 无 |
| `procurement.approve@1` | approver | submitted → approved，不能批自己创建的 | 无（`NoEventReason`） |
| `procurement.receive@1` | requester | 对一个 approved 采购单追加验收记录（amount > 0） | 无 |
| `procurement.close@2` | approver | 1–100 个 approved 采购单，验收合计 ≥ 采购金额，整批 approved → closed；`reason` 必填（`close@1` 已退役） | `procurement.purchase.closed@1`，载荷 `{id, amount, received}` |

**数据库不变量**（migration `0007`，触发器，对任何写路径包括直接 SQL 都成立）：状态只能 submitted → approved → closed，closed 是终态；批准时 `app.actor` 不能为空，也不能是创建人；关闭时验收合计必须 ≥ 金额；采购单的身份字段和金额不可修改；插入验收时触发器会锁住父单，并要求它处于 approved。`TestDatabaseInvariantsHoldForRawSQL` 和 `TestReceiptTriggerSerializesWithClose` 做正反控制，去掉触发器后两项都失败。领域代码仍会先行检查，这样客户端拿到的是统一的 Denied，而不是内部错误。

**父单锁协议**：每个修改采购单或其验收依据的操作，先用 `lockPurchases` 按 `id COLLATE "C"` 顺序 `FOR UPDATE` 锁住全部目标采购单，再在之后的语句里读取状态与验收合计。Read Committed 下，后续语句能看到加锁前已提交的所有验收记录，而被锁采购单在此期间不会再提交新验收。验收记录只有 INSERT 授权（append-only）。以后新增影响金额、状态或验收的写路径必须遵守同一协议，或改用 Serializable 并设计重试。

受控并发测试用测试钩子在持锁后暂停一方，等另一方在 `pg_stat_activity` 中出现锁等待后再放行：

- `TestCloseBeforeReceiveRefusesReceipt`：关闭先持锁，验收等待后看到 closed 被拒。把 receive 改为不加锁读状态，该测试失败（验收落在已关闭单上）。
- `TestReceiveBeforeCloseCountsReceipt`：验收先持锁，关闭等待后计入该验收并成功。把求和挪到加锁之前，该测试失败。

**HTTP**（`bin/product`，demo 身份；独立于 `cmd/server`，因为冻结的 benchmark 会原样导出 `cmd/server/main.go`）：`POST /v1/operations/{name}/{version}`（`Idempotency-Key` 头），`GET /v1/purchases`。错误映射：Invalid 400、Denied 403、Conflict 409、Retryable 503、Internal 500；响应头 `Idempotent-Replayed`。列表只序列化 `id,title,amount,status,createdBy,received`。

**迁移**：`ADMIN_DATABASE_URL=... bin/appctl migrate up`，用 golang-migrate v4.19.1（版本表 `procurement_schema_migrations`），重复运行返回 `applied:false`，dirty 拒绝继续。前置条件：集群中已有 NOLOGIN 角色 `enterprise_runtime` 与 `enterprise_consumer`，应用和消费者登录角色分别是其成员（非 owner、NOSUPERUSER、NOBYPASSRLS）。运行步骤见 [本地运行](getting-started.md#运行-v01-产品cmdproduct)。
`appctl verify --base REF` 额外检查 `internal/procurement/migrations`：基线中已发布文件必须逐字节一致且存在，新增文件必须排在已发布文件之后（golang-migrate 不会执行低于当前版本的文件）。version/dirty 不代表内容未漂移，也不发现线上手工 DDL。

选用 v4.19.1 而不是 issue 中核对过的 v4.20.1：后者要求 pgx v5.9.2 和 go 1.25.11，会顺带升级整个仓库的 pgx 与 Go 版本；它的变更集中在其他数据库驱动和构建链，PostgreSQL 驱动只是跟随升级 pgx。需要时可单独升级。

端到端：`scripts/product_smoke.sh`（空库 → 两次 migrate → 启动 server → HTTP 验收、关闭、重放、冲突、列表、outbox）。

## 同库通知消费者（M2）

- `kernel/pg/inbox.sql`（产品 migration `0004` 逐字复制）：`kernel.inbox (consumer, event_id)` 主键，以及 SECURITY DEFINER 函数 `kernel.pending_tenants`。运行角色借助它找出有待处理事件的租户，不读别的租户的行。函数 owner（迁移角色）必须是 superuser 或 BYPASSRLS，因为 kernel 表对 owner 也强制 RLS。
- `pg.New(ctx, pool, pg.Registry{Operations, Consumers})`：每个操作声明的事件必须有注册的消费者，每个消费者处理的事件必须有操作声明，否则启动失败。
- `Runner.Drain(ctx, name)`：按租户绑定身份，每个事件一个事务。先 `INSERT kernel.inbox ... ON CONFLICT DO NOTHING` 认领，再调用 `Handle`，最后一起提交。`Handle` 失败或进程崩溃都会回滚，事件保持待处理；并发或重复投递时只有一方生效。事件版本不在消费者声明中、或载荷不符的事件会报错且不记为已处理，不阻塞其他事件。
- `Runner.Consume`：启动时立即 drain，之后每秒一次；`cmd/product` 在同一进程以 goroutine 运行它（`PRODUCT_CONSUMER=off` 可关闭）。不做租约、Worker 池或外部投递。
- 角色分离（migration `0006`）：消费者用独立登录角色（`enterprise_consumer` 成员，`CONSUMER_DATABASE_URL`），只能读 outbox、写 inbox 和通知、读采购单的 id/创建人；应用角色不能写 inbox/通知、不能调用 `pending_tenants`。`TestConsumerRoleSeparation` 做正反控制。
- `procurement.notify` 把 `procurement.purchase.closed@1` 投影为 `procurement.notification`（`event_id` 唯一），收件人为采购单创建人。
- `GET /v1/activity`：当前租户最近 50 条审计和当前用户的通知。

界面：`apps/web/app/procurement`（`/procurement`），通过 `/api/product/*` 代理到 `cmd/product`（`PRODUCT_HTTP_PORT`，默认 8090），只放行列出的读路径和三个操作。原有 `/` 页面与三条路线的 6 项浏览器用例不变。

## 开发工具（M3）

- `appctl describe`：输出 `pg.Describe(procurement.Registry())` 的 JSON，即 `cmd/product` 交给 `pg.New` 的同一份注册表（操作定义 + 消费者）。提交为 `internal/procurement/contract.json`。
- `appctl validate [--base REF]`：静态写路径规则（`cmd/appctl/writes.go`，只读字符串字面量，跳过测试文件）。`kernel/` 和 `internal/procurement` 以外的代码写 `procurement.*` 或 `kernel.*` 表，报 `WRITE_OUTSIDE_DOMAIN`；领域代码写 `kernel.*` 表，报 `KERNEL_TABLE_WRITE`；领域代码导入 `database/sql`、`pgxpool`、`pgconn` 或调用 `Begin`、`pgx.Connect`，报 `DOMAIN_BYPASSES_SESSION`（`migrate.go` 豁免，pgx 的导入别名也会解析）。检查先对字面量去引号，再查找其中的每一条写语句；看起来像写语句的普通文本同样会被报告。注册表无效或 `contract.json` 过期，报 `CONTRACT_DRIFT`；有 `--base` 时，基线中已发布的操作版本定义（事件、MaxTargets、NoEventReason、Targets）若被原地修改，报 `CONTRACT_CHANGED_WITHOUT_VERSION`。v0.1.1 新增的 `targets` 字段在旧基线中不存在，补上它不算修改。删除旧版本允许（退役）。它看不到 Normalize/Execute 内部行为的变化。
- **冻结行为用例**：`internal/procurement/behavior/<name>@<version>.json` 每个操作版本一份，由 `TestFrozenBehavior` 在真实 PostgreSQL 上逐条执行，比对错误码、结果和指定查询（状态、审计、outbox 载荷）。`appctl validate` 要求每个已注册版本都有文件（`BEHAVIOR_MISSING`）；有 `--base` 时，基线中已发布的文件不能修改（`BEHAVIOR_CHANGED`），只有随版本一起退役时才能删除。执行用例的 `behavior_test.go`（自带 seed、身份和比较逻辑）与 `kernel/pg/pgtest` 发布后同样不能修改（`BEHAVIOR_HARNESS_CHANGED`），`internal/procurement` 的测试中不允许出现 `TestMain`（`BEHAVIOR_HARNESS_BYPASS`）。这样，行为变化要么让冻结用例失败，要么必须改用例；两条路都会被拦下，只能发布新版本。局限：只能发现已有用例覆盖到的行为（例如标题长度边界目前没有冻结）；注册新版本的同时退役旧版本是允许的，这本身就是一次显式的版本变更。例如试验中 reverse 的改动，只有在出现冲正记录时才会改变 close 的结果，而旧用例里不会有冲正记录。
- `appctl verify --base REF`：原有 POC 检查，加上产品 migration 基线检查。`validate` 不并入 `verify`，因为 `tests/verifier.py` 在不含产品代码的局部 checkout 上运行 `verify`。
- 新增操作或版本的步骤：在领域模块实现 `pg.Operation`（需要事件时再实现或扩展 `pg.Consumer`），加入 `Registry()`，运行 `appctl describe > internal/procurement/contract.json`，补测试。不需要修改 `kernel/`。

## 运行测试

```bash
docker run -d --name ea-pg17 -p 127.0.0.1:55417:5432 -e POSTGRES_PASSWORD=postgres postgres:17.6
export KERNEL_TEST_ADMIN_URL='postgres://postgres:postgres@127.0.0.1:55417/postgres?sslmode=disable'
KERNEL_REQUIRE_DB=1 go test -race -count=1 ./kernel/... ./internal/procurement/ ./cmd/appctl/
make build && scripts/product_smoke.sh   # 含消费者关闭→重启→再重启
(cd apps/web && npm ci && npm run build && npx playwright install chromium)
scripts/product_browser.sh               # 4 项浏览器用例
```

每个测试新建并删除自己的数据库，并创建集群级角色 `kernel_test_runtime`、`enterprise_runtime`。只对可销毁的服务器使用。未设置变量时 DB 测试跳过；`KERNEL_REQUIRE_DB=1`（CI `kernel` job）下跳过视为失败。

## 尚未包含

- 旧 `internal/opkernel`、guarded/baseline 模式与原 `/` 页面未迁移到新内核；原页面的创建/审批仍走旧 API。
- approve/receive 没有事件（`NoEventReason`）；只有 close 有消费者。
- 验收记录的冲正/撤销：未开放 UPDATE/DELETE，需要时另做需求。
- 直接 SQL 仍然拿不到操作级的审计、幂等和完整目标检查；状态机、职责分离、验收与关闭的不变量已由 0007 触发器兜底。
- 写路径规则是静态的，看不到运行时拼接出来的 SQL。
